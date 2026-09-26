;;; groket.el --- Live Groket sessions in Org buffers -*- lexical-binding: t; -*-

;; Package-Requires: ((emacs "29.1"))
;; Keywords: tools, org
;; URL: https://github.com/indynull/groket

;;; Commentary:

;; View a running Groket session as an Org buffer and edit operator notes
;; without making the generated trace projection writable.
;;
;; Requires a live control owner (``groket serve start -d`` or TUI auto-serve)
;; on the same Unix socket as other clients (HUD, Vim).

;;; Code:

(require 'cl-lib)
(require 'jsonrpc)
(require 'org)
(require 'subr-x)
(require 'term)

(defgroup groket nil
  "Live Groket session buffers."
  :group 'tools)

(defcustom groket-executable "groket"
  "Executable used by `groket-start'."
  :type 'string)

(defcustom groket-control-socket nil
  "Control socket path, or nil to use the per-user runtime default."
  :type '(choice (const :tag "Runtime default" nil) file))

(defcustom groket-request-timeout 10
  "Seconds to wait for a control request."
  :type 'number)

(defcustom groket-auto-refresh t
  "When non-nil, reload the session projection on remote notes/trace changes.
If the buffer has unsaved edits, auto-refresh is skipped and a message is shown
instead (same as when this option is nil)."
  :type 'boolean)

(defvar groket--connection nil)
(defvar groket--terminal-buffer nil)
(defvar-local groket-session-id nil)
(defvar-local groket-session-reference nil)
(defvar-local groket-notes-revision nil)
(defvar-local groket-notes-stale nil)
(defvar-local groket-session-stale nil)
(defvar-local groket--rendered-note-ids nil
  "Note ids present in the document rendered into this buffer.
Only these ids may be pushed back to the server; anything else was typed
into the buffer and does not name a note.")

(defun groket--socket-path ()
  "Return the expanded control socket path."
  (expand-file-name
   (or groket-control-socket
       (let ((runtime (getenv "XDG_RUNTIME_DIR")))
         (if (and runtime (not (string-empty-p runtime)))
             (expand-file-name "groket/control.sock" runtime)
           (expand-file-name "~/.groket/run/control.sock"))))))

(defun groket--method-name (method)
  "Return METHOD as a protocol string."
  (if (symbolp method) (symbol-name method) method))

(defun groket--maybe-auto-refresh (reason)
  "Reload the projection after a remote change, or message how to reload.
REASON is a short human label (e.g. \"notes changed\")."
  (cond
   ((not groket-auto-refresh)
    (message "Groket: %s — C-c C-r (or gr) to reload" reason))
   ((buffer-modified-p)
    (message "Groket: %s — unsaved note edits; save or C-c C-r to reload" reason))
   (t
    (condition-case err
        (progn
          (groket--do-refresh)
          (message "Groket: reloaded (%s)" reason))
      (error
       (message "Groket: auto-refresh failed: %s" (error-message-string err)))))))

(defun groket--notification (_connection method params)
  "Handle a server notification named METHOD with PARAMS."
  (let ((session (plist-get params :sessionId)))
    (dolist (buffer (buffer-list))
      (with-current-buffer buffer
        (when (and (derived-mode-p 'groket-session-mode)
                   (or (null session) (equal groket-session-id session)))
          (pcase (groket--method-name method)
            ("notes/changed"
             (let ((rev (plist-get params :revision)))
               ;; Matching revision is the echo of our own upsert/delete, not external drift.
               (if (and rev (equal rev groket-notes-revision))
                   (setq groket-notes-stale nil)
                 (let ((was groket-notes-stale))
                   (setq groket-notes-stale t)
                   (unless was
                     (groket--maybe-auto-refresh "notes changed"))))))
            ("session/changed"
             (let ((was groket-session-stale))
               (setq groket-session-stale t)
               (unless was
                 (groket--maybe-auto-refresh "session trace changed"))))
            ("session/selected"
             (let ((prompt-index (plist-get params :promptIndex)))
               (when prompt-index
                 (goto-char (point-min))
                 ;; Prefer the outline headline (readable); property may be folded away.
                 (or (re-search-forward
                      (format "^\\* Prompt %s$" prompt-index)
                      nil t)
                     (re-search-forward
                      (format "^:GROKET_PROMPT_INDEX: %s$" prompt-index)
                      nil t))
                 (beginning-of-line)))))
          (force-mode-line-update))))))

(defun groket--make-network-process (_connection)
  "Create the local process used by a JSON-RPC CONNECTION."
  (make-network-process
   :name "groket-control"
   :family 'local
   :service (groket--socket-path)
   :coding 'utf-8-unix
   :noquery t))

(defun groket-connected-p ()
  "Return non-nil when the editor control connection is live."
  (and groket--connection
       (jsonrpc-running-p groket--connection)))

(defun groket--drop-connection ()
  "Shut the control connection down and forget it."
  (when groket--connection
    (ignore-errors (jsonrpc-shutdown groket--connection))
    (setq groket--connection nil)))

(defun groket--request (connection method params)
  "Send METHOD with PARAMS over CONNECTION and return the result.
A connection whose peer died is dropped so the next command reconnects."
  (condition-case err
      (jsonrpc-request connection method params
                       :timeout groket-request-timeout)
    (error
     (unless (groket-connected-p)
       (groket--drop-connection))
     (signal (car err) (cdr err)))))

(defun groket-disconnect ()
  "Close the editor control connection."
  (interactive)
  (groket--drop-connection))

(defun groket-connect ()
  "Connect to the running Groket control socket."
  (interactive)
  (unless (groket-connected-p)
    (unless (file-exists-p (groket--socket-path))
      (user-error "Groket control socket does not exist: %s" (groket--socket-path)))
    (condition-case err
        (progn
          (setq groket--connection
                (make-instance
                 'jsonrpc-process-connection
                 :name "groket"
                 :process #'groket--make-network-process
                 :notification-dispatcher #'groket--notification
                 :events-buffer-config '(:size 200)))
          (groket--request
           groket--connection
           "initialize"
           `(:protocolVersion 1
             :clientInfo (:name "Emacs" :version ,emacs-version))))
      (error
       (groket--drop-connection)
       (signal (car err) (cdr err)))))
  groket--connection)

(defun groket--wait-for-socket (process)
  "Wait for the control socket owned by PROCESS."
  (let ((deadline (+ (float-time) groket-request-timeout)))
    (while (and (process-live-p process)
                (not (file-exists-p (groket--socket-path)))
                (< (float-time) deadline))
      (accept-process-output process 0.05))
    (unless (file-exists-p (groket--socket-path))
      (user-error "Groket did not create its control socket"))))

(defun groket-start (&optional session prompt-index)
  "Start the TUI for SESSION and optionally select PROMPT-INDEX."
  (interactive)
  (let* ((socket (groket--socket-path))
         (args (append
                (when session (list "--path" (expand-file-name session)))
                (list "--control-socket" socket)
                (when prompt-index
                  (list "--prompt-index" (number-to-string prompt-index)))))
         (buffer (apply #'make-term "groket-live" groket-executable nil args))
         (process (get-buffer-process buffer)))
    (setq groket--terminal-buffer buffer)
    (set-process-query-on-exit-flag process nil)
    (with-current-buffer buffer
      (term-mode)
      (term-char-mode))
    (display-buffer buffer '(display-buffer-at-bottom (window-height . 14)))
    (groket--wait-for-socket process)
    buffer))

(defun groket--connection-refused-p (err)
  "Return non-nil when ERR is a transient control-socket connect failure.
Matches connection refused, missing path, and macOS EAGAIN
\(\"Resource temporarily unavailable\" / os error 35\)."
  (or (eq (car err) 'file-error)
      (let ((message (downcase (error-message-string err))))
        (or (string-match-p "connection refused" message)
            (string-match-p "no such file or directory" message)
            (string-match-p "resource temporarily unavailable" message)
            (string-match-p "os error 35" message)))))

(defun groket--connect-retrying (deadline)
  "Connect, retrying refused connections until DEADLINE.
A TUI taking a stale socket over needs a moment before it accepts clients."
  (let (connected)
    (while (not connected)
      (condition-case err
          (progn (groket-connect) (setq connected t))
        (error
         (groket--drop-connection)
         (when (or (not (groket--connection-refused-p err))
                   (>= (float-time) deadline))
           (signal (car err) (cdr err)))
         (sleep-for 0.1)))))
  groket--connection)

(defun groket--connection-for-session (session)
  "Return a live connection, starting Groket for SESSION when needed."
  (unless (groket-connected-p)
    (let ((directory (and session
                          (file-directory-p session)
                          session)))
      (unless (file-exists-p (groket--socket-path))
        (groket-start directory nil))
      (condition-case err
          (groket-connect)
        (error
         (groket--drop-connection)
         ;; A socket file outliving its TUI refuses connections; starting the
         ;; TUI again takes the stale socket over.
         (unless (and directory (groket--connection-refused-p err))
           (signal (car err) (cdr err)))
         (groket-start directory nil)
         (groket--connect-retrying (+ (float-time) groket-request-timeout))))))
  groket--connection)

(defun groket--normalize-session-reference (session)
  "Expand SESSION when it names a directory and preserve catalog ids."
  (if (file-directory-p session)
      (file-truename session)
    session))

(defun groket--field-body-region ()
  "Return the body region of the Org field at point.
The region ends with the newline of the last content line, so the blank
separator line before the next heading stays outside the writable span and
Org structure cannot be typed at column 0 inside a field."
  (save-excursion
    (org-back-to-heading t)
    (let ((limit (org-entry-end-position)))
      (org-end-of-meta-data t)
      (let ((begin (point))
            (end limit))
        (save-excursion
          (goto-char limit)
          (skip-chars-backward " \t\n" begin)
          ;; A body of blank lines only has no content line to stop after.
          (when (> (point) begin)
            (forward-line 1)
            (setq end (min limit (point)))))
        (cons begin (max begin end))))))

(defun groket--editable-field-regions ()
  "Return body regions for headings with a GROKET_FIELD_ID property."
  (let (regions)
    (org-map-entries
     (lambda ()
       (when (org-entry-get nil "GROKET_FIELD_ID" nil)
         (push (groket--field-body-region) regions)))
     nil nil)
    regions))

(defun groket--document-note-ids ()
  "Return the GROKET_NOTE_ID values present in the current buffer."
  (let (ids)
    (org-map-entries
     (lambda ()
       (let ((note-id (org-entry-get nil "GROKET_NOTE_ID" nil)))
         (when note-id (push note-id ids))))
     nil nil)
    (nreverse ids)))

(defun groket--apply-document (text session-id revision reference)
  "Replace the current buffer with TEXT and configure session metadata."
  (let ((inhibit-read-only t))
    (erase-buffer)
    (insert text)
    (goto-char (point-min))
    (setq groket--rendered-note-ids (groket--document-note-ids))
    (goto-char (point-min))
    (let ((regions (groket--editable-field-regions)))
      (add-text-properties
       (point-min) (point-max)
       '(read-only t front-sticky (read-only) rear-nonsticky (read-only)))
      (dolist (region regions)
        (remove-text-properties (car region) (cdr region) '(read-only nil)))))
  (setq groket-session-id session-id
        groket-session-reference reference
        groket-notes-revision revision
        groket-notes-stale nil
        groket-session-stale nil)
  (set-buffer-modified-p nil)
  ;; Native src fontify (Markdown transcript); keep body un-indented for tables.
  (setq-local org-src-fontify-natively t)
  (setq-local org-src-preserve-indentation t)
  (setq-local org-edit-src-content-indentation 0)
  ;; Pipe tables stay aligned only when lines are not soft-wrapped.
  (setq-local truncate-lines t)
  (setq-local org-hide-drawer-startup t)
  (when (fboundp 'org-restart-font-lock)
    (org-restart-font-lock))
  ;; Fold property drawers (machine tags) for a calmer outline.
  (save-excursion
    (goto-char (point-min))
    (when (fboundp 'org-cycle-hide-drawers)
      (org-cycle-hide-drawers 'all)))
  (goto-char (point-min)))

(defun groket--ancestor-property (property)
  "Return PROPERTY from the nearest heading ancestor."
  (save-excursion
    (org-back-to-heading t)
    (catch 'found
      (while t
        (let ((value (org-entry-get nil property nil)))
          (when value (throw 'found value)))
        (unless (org-up-heading-safe) (throw 'found nil))))))

(defun groket--prompt-index-at-point ()
  "Return the source prompt index at point."
  (let ((raw (groket--ancestor-property "GROKET_PROMPT_INDEX")))
    (and raw (string-to-number raw))))

(defun groket--strip-org-fixed-line (line)
  "Undo Org fixed-width prefix (`: ' / `:') from a rendered field LINE."
  (cond
   ((string-prefix-p ": " line) (substring line 2))
   ((string-equal ":" line) "")
   (t line)))

(defun groket--field-value-at-point ()
  "Return the current field body without generated properties.
Strips Org fixed-width lines used when rendering field values so outline
stars inside a value cannot form headlines, then round-trip cleanly.
Leading and trailing blank lines are part of the value."
  (pcase-let ((`(,begin . ,end) (groket--field-body-region)))
    (let* ((raw (buffer-substring-no-properties begin end))
           ;; Region end is the newline after the last content line; that
           ;; delimiter is not part of the value.
           (raw (if (string-suffix-p "\n" raw)
                    (substring raw 0 -1)
                  raw))
           ;; Keep empty lines (omit-nulls nil).
           (lines (split-string raw "\n" nil)))
      (mapconcat #'groket--strip-org-fixed-line lines "\n"))))

(defun groket--note-at-point ()
  "Return the operator note containing point as a JSON-ready plist."
  (save-excursion
    (let ((note-id (groket--ancestor-property "GROKET_NOTE_ID")))
      (unless note-id (user-error "Point is not inside an operator note"))
      (while (not (org-entry-get nil "GROKET_NOTE_ID" nil))
        (org-up-heading-safe))
      (let* ((note-start (point))
             (note-end (save-excursion (org-end-of-subtree t t) (point)))
             (turn-index (string-to-number
                          (or (groket--ancestor-property "GROKET_TURN_INDEX") "0")))
             (event-text (or (org-entry-get nil "GROKET_EVENT_INDICES" nil) ""))
             (created-at (or (org-entry-get nil "GROKET_CREATED_AT" nil) ""))
             (updated-at (format-time-string "%FT%T%:z" nil t))
             (fields (make-hash-table :test #'equal)))
        (save-restriction
          (narrow-to-region note-start note-end)
          (org-map-entries
           (lambda ()
             (let ((field-id (org-entry-get nil "GROKET_FIELD_ID" nil)))
               (when field-id
                 (puthash field-id (groket--field-value-at-point) fields))))
           nil 'tree))
        `(:id ,note-id
          :turnIndex ,turn-index
          :fields ,fields
          :eventIndices ,(vconcat
                          (mapcar #'string-to-number
                                  (split-string event-text "," t "[[:space:]]+")))
          :createdAt ,created-at
          :updatedAt ,updated-at)))))

(defun groket--render-session (session)
  "Request the Org projection for SESSION."
  (groket--request
   (groket--connection-for-session session)
   "session/render"
   `(:session ,session)))

(defun groket--do-refresh ()
  "Reload the projection without prompting (caller checks dirty state).
Changes announced while the render request is in flight postdate the
response, so their stale flags survive the reload."
  (let* ((reference (or groket-session-reference groket-session-id))
         (point-before (point))
         (notes-stale groket-notes-stale)
         (session-stale groket-session-stale)
         result)
    (setq groket-notes-stale nil
          groket-session-stale nil)
    (condition-case err
        (setq result (groket--render-session reference))
      (error
       (setq groket-notes-stale (or groket-notes-stale notes-stale)
             groket-session-stale (or groket-session-stale session-stale))
       (signal (car err) (cdr err))))
    (setq notes-stale groket-notes-stale
          session-stale groket-session-stale)
    (groket--apply-document
     (plist-get result :text)
     (plist-get result :sessionId)
     (plist-get result :notesRevision)
     reference)
    (setq groket-notes-stale notes-stale
          groket-session-stale session-stale)
    (force-mode-line-update)
    (goto-char (min point-before (point-max)))))

(defun groket-refresh ()
  "Reload the current session projection from Groket."
  (interactive)
  (when (and (buffer-modified-p)
             (not (yes-or-no-p "Discard unsaved note edits? ")))
    (user-error "Refresh cancelled"))
  (groket--do-refresh))

(defun groket--session-list (&optional query limit)
  "Return the `session/list' result for QUERY and optional LIMIT."
  (let ((params (list :query (or query ""))))
    (when limit
      (setq params (plist-put params :limit limit)))
    (groket--request (groket-connect) "session/list" params)))

(defun groket--session-entry-path (entry)
  "Return a stable open reference for session ENTRY."
  (or (plist-get entry :path)
      (plist-get entry :sessionId)))

(defun groket--session-entry-annotation (entry)
  "Return a one-line label for catalog ENTRY."
  (let* ((title (or (plist-get entry :title) (plist-get entry :label) ""))
         (session-id (or (plist-get entry :sessionId) ""))
         (status (or (plist-get entry :status) ""))
         (model (or (plist-get entry :model) ""))
         (origin (or (plist-get entry :origin) ""))
         (head (if (and title (not (string-empty-p title)))
                   title
                 session-id)))
    (string-trim
     (mapconcat #'identity
                (delq nil
                      (list head
                            (and status (not (string-empty-p status)) status)
                            (and model (not (string-empty-p model)) model)
                            (and origin (not (string-empty-p origin)) origin)
                            ;; Avoid "id · id" when the title fell back to session-id.
                            (and session-id
                                 (not (string-empty-p session-id))
                                 (not (string-equal session-id head))
                                 session-id)))
                "  ·  "))))

(defun groket-list-sessions (&optional query)
  "List sessions from the running TUI catalog, optionally filtered by QUERY.
With a prefix argument, prompt for QUERY. Results open a read-only buffer."
  (interactive
   (list (if current-prefix-arg
             (read-string "Filter sessions: ")
           "")))
  (groket-connect)
  (let* ((result (groket--session-list query))
         (sessions (append (plist-get result :sessions) nil))
         (total (or (plist-get result :total) 0))
         (matched (or (plist-get result :matched) (length sessions)))
         (buffer (get-buffer-create "*groket-sessions*")))
    (with-current-buffer buffer
      (let ((inhibit-read-only t))
        (erase-buffer)
        (insert
         (format "Groket sessions  matched %s / total %s"
                 matched total)
         (if (and query (not (string-empty-p query)))
             (format "  filter: %s\n\n" query)
           "\n\n"))
        (if (null sessions)
            (insert "(no sessions)\n")
          (dolist (entry sessions)
            (let ((path (groket--session-entry-path entry))
                  (line (groket--session-entry-annotation entry)))
              (insert-text-button
               line
               'action (lambda (_button)
                         (groket-open-session path))
               'follow-link t
               'groket-session path
               'help-echo path)
              (insert "\n")))))
      (goto-char (point-min))
      (setq buffer-read-only t)
      (special-mode))
    (pop-to-buffer buffer)
    buffer))

(defun groket-find-session (&optional query)
  "Pick a catalog session with completion and open it as an Org buffer.
QUERY pre-filters the catalog on the server when non-empty. With a prefix
argument, prompt for QUERY first."
  (interactive
   (list (if current-prefix-arg
             (read-string "Filter sessions: ")
           "")))
  (groket-connect)
  (let* ((result (groket--session-list query))
         (sessions (append (plist-get result :sessions) nil))
         (table (make-hash-table :test #'equal))
         candidates)
    (when (null sessions)
      (user-error "No sessions matched%s"
                  (if (and query (not (string-empty-p query)))
                      (format " %S" query)
                    "")))
    (dolist (entry sessions)
      (let* ((annotation (groket--session-entry-annotation entry))
             (path (groket--session-entry-path entry))
             (key annotation)
             (n 2))
        (while (gethash key table)
          (setq key (format "%s (%s)" annotation n)
                n (1+ n)))
        (puthash key path table)
        (push key candidates)))
    (let* ((choice (completing-read "Groket session: " (nreverse candidates) nil t))
           (path (gethash choice table)))
      (unless path
        (user-error "No session selected"))
      (groket-open-session path))))

(defun groket-open-session (session &optional prompt-index)
  "Open SESSION as an Org buffer and select PROMPT-INDEX in the TUI."
  (interactive (list (read-string "Session path or id: ") nil))
  (let* ((reference (groket--normalize-session-reference session))
         (connection (groket--connection-for-session reference))
         (result (groket--request
                  connection "session/render" `(:session ,reference)))
         (session-id (plist-get result :sessionId))
         (name (format "*groket:%s*" session-id))
         (existing (get-buffer name))
         ;; Re-rendering an open buffer throws its edits away, so ask first.
         (render (or (null existing)
                     (not (buffer-modified-p existing))
                     (yes-or-no-p
                      (format "%s has unsaved note edits; discard them? " name))))
         (buffer (or existing (get-buffer-create name))))
    (when render
      (with-current-buffer buffer
        (groket-session-mode)
        (groket--apply-document
         (plist-get result :text)
         session-id
         (plist-get result :notesRevision)
         reference)))
    (groket--request
     connection "session/open"
     `(:session ,reference :promptIndex ,prompt-index))
    (pop-to-buffer buffer)
    buffer))
(defun groket-open-prompt-at-point ()
  "Select the prompt at point in the running TUI."
  (interactive)
  (let ((prompt-index (groket--prompt-index-at-point)))
    (unless prompt-index (user-error "Point is not inside a prompt"))
    (groket--request
     (groket-connect) "session/open"
     `(:session ,groket-session-reference :promptIndex ,prompt-index))))

(defun groket--rendered-note-id-p (note-id)
  "Return non-nil when NOTE-ID belongs to the rendered document."
  (and note-id (member note-id groket--rendered-note-ids) t))

(defun groket-save-note ()
  "Save the operator note at point with revision checking.
The buffer stays modified: other notes may still hold unsaved edits."
  (interactive)
  (let ((note (groket--note-at-point)))
    (unless (groket--rendered-note-id-p (plist-get note :id))
      (user-error "Note %s is not part of this session projection"
                  (plist-get note :id)))
    (let ((result
           (groket--request
            (groket-connect) "notes/upsert"
            `(:session ,groket-session-reference
              :expectedRevision ,groket-notes-revision
              :note ,note))))
      (setq groket-notes-revision (plist-get result :revision)
            groket-notes-stale nil)
      (force-mode-line-update)
      result)))

(defun groket--new-note-id ()
  "Return a locally unique note id."
  (concat
   "n-"
   (substring
    (secure-hash 'sha256 (format "%s:%s:%s" (float-time) (random) (emacs-pid)))
    0 12)))

(defun groket--require-saved (action)
  "Refuse ACTION while the buffer holds unsaved note edits.
ACTION reloads the projection, which would drop those edits."
  (when (buffer-modified-p)
    (user-error "Unsaved note edits; C-x C-s to save or C-c C-r to reload before %s"
                action)))

(defun groket-new-note ()
  "Create an operator note under the prompt at point."
  (interactive)
  (groket--require-saved "creating a note")
  (let* ((turn-text (groket--ancestor-property "GROKET_TURN_INDEX"))
         (prompt-index (groket--prompt-index-at-point)))
    (unless (and turn-text prompt-index)
      (user-error "Point is not inside a prompt"))
    (let* ((connection (groket-connect))
           (listed (groket--request
                    connection "notes/list"
                    `(:session ,groket-session-reference)))
           (schema (plist-get listed :schema))
           (field-specs (plist-get schema :fields))
           (fields (make-hash-table :test #'equal))
           (timestamp (format-time-string "%FT%T%:z" nil t))
           (note-id (groket--new-note-id))
           (note
            `(:id ,note-id
              :turnIndex ,(string-to-number turn-text)
              :fields ,fields
              :eventIndices []
              :createdAt ,timestamp
              :updatedAt ,timestamp)))
      (mapc
       (lambda (spec)
         (let ((field-id (plist-get spec :id)))
           (when field-id (puthash field-id "" fields))))
       field-specs)
      (let ((result
             (groket--request
              connection "notes/upsert"
              `(:session ,groket-session-reference
                :expectedRevision ,groket-notes-revision
                :note ,note))))
        (setq groket-notes-revision (plist-get result :revision))
        ;; The note exists on the server; reload without a prompt that could
        ;; abort and leave the buffer disagreeing with it.
        (groket--do-refresh)
        (goto-char (point-min))
        (when (re-search-forward
               (format "^:GROKET_NOTE_ID: %s$" (regexp-quote note-id))
               nil t)
          (org-back-to-heading t)
          ;; Prefer first field body (editable) under the note.
          (when (re-search-forward "^:GROKET_FIELD_ID:" nil t)
            (org-end-of-meta-data t)))))))

(defun groket-delete-note (&optional no-confirm)
  "Delete the note at point, asking first unless NO-CONFIRM is non-nil."
  (interactive)
  (groket--require-saved "deleting a note")
  (let ((note-id (groket--ancestor-property "GROKET_NOTE_ID")))
    (unless note-id (user-error "Point is not inside an operator note"))
    (when (or no-confirm (yes-or-no-p (format "Delete note %s? " note-id)))
      (let ((result
             (groket--request
              (groket-connect) "notes/delete"
              `(:session ,groket-session-reference
                :expectedRevision ,groket-notes-revision
                :noteId ,note-id))))
        (setq groket-notes-revision (plist-get result :revision))
        ;; The delete happened; reload without a prompt that could abort.
        (groket--do-refresh)))))

(defun groket-save-buffer ()
  "Save every operator note in the current session buffer.
Headings carrying a note id the projection never rendered name no note on
the server, so they are reported and left alone."
  (interactive)
  (save-excursion
    (let (entries skipped)
      (goto-char (point-min))
      (org-map-entries
       (lambda ()
         (let ((note-id (org-entry-get nil "GROKET_NOTE_ID" nil)))
           (when note-id
             (push (cons note-id (copy-marker (point))) entries))))
       nil nil)
      (dolist (entry (nreverse entries))
        (if (groket--rendered-note-id-p (car entry))
            (progn
              (goto-char (cdr entry))
              (groket-save-note))
          (push (car entry) skipped)))
      (when skipped
        (message "Groket: skipped %d unknown note id(s): %s"
                 (length skipped)
                 (mapconcat #'identity (nreverse skipped) ", ")))))
  (set-buffer-modified-p nil)
  t)

(defun groket--other-session-buffer-p ()
  "Return non-nil when another live session buffer exists."
  (cl-some (lambda (buffer)
             (and (not (eq buffer (current-buffer)))
                  (buffer-live-p buffer)
                  (with-current-buffer buffer
                    (derived-mode-p 'groket-session-mode))))
           (buffer-list)))

(defun groket--kill-buffer-hook ()
  "Drop the control connection with the last session buffer.
The TUI terminal buffer stays: it belongs to the user, not to this client."
  (unless (groket--other-session-buffer-p)
    (groket--drop-connection)))

(defvar-keymap groket-session-mode-map
  :parent org-mode-map
  ;; Do not bind bare ``g`` — in Evil/Doom it is a motion prefix (``gg``, …).
  "C-c C-r" #'groket-refresh
  "C-c C-n" #'groket-new-note
  "C-c C-k" #'groket-delete-note
  "C-c C-o" #'groket-open-prompt-at-point
  "C-c C-c" #'groket-save-note
  "C-x C-s" #'groket-save-buffer)

(define-derived-mode groket-session-mode org-mode "Groket"
  "Major mode for live Groket Org session buffers.

Transcript is read-only Markdown in source blocks; only note field bodies edit.
Keys: C-c C-c save note, C-x C-s save all, C-c C-n new note, C-c C-k delete,
C-c C-o select prompt in TUI, C-c C-r refresh. In Doom/Evil, gr also refreshes."
  (setq-local write-contents-functions '(groket-save-buffer))
  (add-hook 'kill-buffer-hook #'groket--kill-buffer-hook nil t)
  (setq-local org-src-fontify-natively t)
  (setq-local org-src-preserve-indentation t)
  (setq-local org-edit-src-content-indentation 0)
  (setq-local truncate-lines t)
  (setq-local org-hide-drawer-startup t)
  (setq-local mode-line-process
              '(:eval
                (concat
                 (when groket-session-stale " Trace changed")
                 (when groket-notes-stale " Notes changed")))))

;; Evil/Doom: gr refresh without stealing g (motion prefix).
;; `evil-define-key*' is a function, so this body survives byte compilation
;; without Evil loaded at compile time.
(declare-function evil-define-key* "evil-core" (state keymap key def &rest bindings))

(with-eval-after-load 'evil
  (when (fboundp 'evil-define-key*)
    (evil-define-key* 'normal groket-session-mode-map (kbd "gr") #'groket-refresh)))

(provide 'groket)
;;; groket.el ends here
