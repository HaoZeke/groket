;;; groket.el --- Live Groket sessions in Org buffers -*- lexical-binding: t; -*-

;; Package-Requires: ((emacs "29.1"))
;; Keywords: tools, org
;; URL: https://github.com/indynull/groket

;;; Commentary:

;; View a running Groket session as an Org buffer and edit operator notes
;; without making the generated trace projection writable.

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

(defvar groket--connection nil)
(defvar groket--terminal-buffer nil)
(defvar-local groket-session-id nil)
(defvar-local groket-session-reference nil)
(defvar-local groket-notes-revision nil)
(defvar-local groket-notes-stale nil)
(defvar-local groket-session-stale nil)

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

(defun groket--notification (_connection method params)
  "Handle a server notification named METHOD with PARAMS."
  (let ((session (plist-get params :sessionId)))
    (dolist (buffer (buffer-list))
      (with-current-buffer buffer
        (when (and (derived-mode-p 'groket-session-mode)
                   (or (null session) (equal groket-session-id session)))
          (pcase (groket--method-name method)
            ("notes/changed" (setq groket-notes-stale t))
            ("session/changed" (setq groket-session-stale t))
            ("session/selected"
             (let ((prompt-index (plist-get params :promptIndex)))
               (when prompt-index
                 (goto-char (point-min))
                 (re-search-forward
                  (format "^:GROKET_PROMPT_INDEX: %s$" prompt-index)
                  nil t)))))
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

(defun groket-disconnect ()
  "Close the editor control connection."
  (interactive)
  (when groket--connection
    (jsonrpc-shutdown groket--connection)
    (setq groket--connection nil)))

(defun groket-connect ()
  "Connect to the running Groket control socket."
  (interactive)
  (unless (groket-connected-p)
    (unless (file-exists-p (groket--socket-path))
      (user-error "Groket control socket does not exist: %s" (groket--socket-path)))
    (setq groket--connection
          (make-instance
           'jsonrpc-process-connection
           :name "groket"
           :process #'groket--make-network-process
           :notification-dispatcher #'groket--notification
           :events-buffer-config '(:size 200)))
    (jsonrpc-request
     groket--connection
     "initialize"
     `(:protocolVersion 1
       :clientInfo (:name "Emacs" :version ,emacs-version))
     :timeout groket-request-timeout))
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

(defun groket--connection-for-session (session)
  "Return a live connection, starting Groket for SESSION when needed."
  (unless (groket-connected-p)
    (unless (file-exists-p (groket--socket-path))
      (groket-start (and (file-directory-p session) session) nil))
    (groket-connect))
  groket--connection)

(defun groket--normalize-session-reference (session)
  "Expand SESSION when it names a directory and preserve catalog ids."
  (if (file-directory-p session)
      (file-truename session)
    session))

(defun groket--field-body-region ()
  "Return the body region of the Org field at point."
  (save-excursion
    (org-back-to-heading t)
    (let ((end (org-entry-end-position)))
      (org-end-of-meta-data t)
      (cons (point) end))))

(defun groket--editable-field-regions ()
  "Return body regions for headings with a GROKET_FIELD_ID property."
  (let (regions)
    (org-map-entries
     (lambda ()
       (when (org-entry-get nil "GROKET_FIELD_ID" nil)
         (push (groket--field-body-region) regions)))
     nil nil)
    regions))

(defun groket--apply-document (text session-id revision reference)
  "Replace the current buffer with TEXT and configure session metadata."
  (let ((inhibit-read-only t))
    (erase-buffer)
    (insert text)
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

(defun groket--field-value-at-point ()
  "Return the current field body without generated properties."
  (pcase-let ((`(,begin . ,end) (groket--field-body-region)))
    (string-trim (buffer-substring-no-properties begin end))))

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
  (jsonrpc-request
   (groket--connection-for-session session)
   "session/render"
   `(:session ,session)
   :timeout groket-request-timeout))

(defun groket-refresh ()
  "Reload the current session projection from Groket."
  (interactive)
  (when (and (buffer-modified-p)
             (not (yes-or-no-p "Discard unsaved note edits? ")))
    (user-error "Refresh cancelled"))
  (let* ((reference (or groket-session-reference groket-session-id))
         (result (groket--render-session reference))
         (point-before (point)))
    (groket--apply-document
     (plist-get result :text)
     (plist-get result :sessionId)
     (plist-get result :notesRevision)
     reference)
    (goto-char (min point-before (point-max)))))

(defun groket-open-session (session &optional prompt-index)
  "Open SESSION as an Org buffer and select PROMPT-INDEX in the TUI."
  (interactive (list (read-string "Session path or id: ") nil))
  (let* ((reference (groket--normalize-session-reference session))
         (connection (groket--connection-for-session reference))
         (result (jsonrpc-request
                  connection "session/render" `(:session ,reference)
                  :timeout groket-request-timeout))
         (session-id (plist-get result :sessionId))
         (buffer (get-buffer-create (format "*groket:%s*" session-id))))
    (with-current-buffer buffer
      (groket-session-mode)
      (groket--apply-document
       (plist-get result :text)
       session-id
       (plist-get result :notesRevision)
       reference))
    (jsonrpc-request
     connection "session/open"
     `(:session ,reference :promptIndex ,prompt-index)
     :timeout groket-request-timeout)
    (pop-to-buffer buffer)
    buffer))

(defun groket-open-prompt-at-point ()
  "Select the prompt at point in the running TUI."
  (interactive)
  (let ((prompt-index (groket--prompt-index-at-point)))
    (unless prompt-index (user-error "Point is not inside a prompt"))
    (jsonrpc-request
     (groket-connect) "session/open"
     `(:session ,groket-session-reference :promptIndex ,prompt-index)
     :timeout groket-request-timeout)))

(defun groket-save-note ()
  "Save the operator note at point with revision checking."
  (interactive)
  (let* ((result
          (jsonrpc-request
           (groket-connect) "notes/upsert"
           `(:session ,groket-session-reference
             :expectedRevision ,groket-notes-revision
             :note ,(groket--note-at-point))
           :timeout groket-request-timeout)))
    (setq groket-notes-revision (plist-get result :revision)
          groket-notes-stale nil)
    (set-buffer-modified-p nil)
    (force-mode-line-update)
    result))

(defun groket--new-note-id ()
  "Return a locally unique note id."
  (concat
   "n-"
   (substring
    (secure-hash 'sha256 (format "%s:%s:%s" (float-time) (random) (emacs-pid)))
    0 12)))

(defun groket-new-note ()
  "Create an operator note under the prompt at point."
  (interactive)
  (let* ((turn-text (groket--ancestor-property "GROKET_TURN_INDEX"))
         (prompt-index (groket--prompt-index-at-point)))
    (unless (and turn-text prompt-index)
      (user-error "Point is not inside a prompt"))
    (let* ((connection (groket-connect))
           (listed (jsonrpc-request
                    connection "notes/list"
                    `(:session ,groket-session-reference)
                    :timeout groket-request-timeout))
           (schema (plist-get listed :schema))
           (field-specs (plist-get schema :fields))
           (fields (make-hash-table :test #'equal))
           (timestamp (format-time-string "%FT%T%:z" nil t)))
      (mapc
       (lambda (spec)
         (let ((field-id (plist-get spec :id)))
           (when field-id (puthash field-id "" fields))))
       field-specs)
      (let* ((note
              `(:id ,(groket--new-note-id)
                :turnIndex ,(string-to-number turn-text)
                :fields ,fields
                :eventIndices []
                :createdAt ,timestamp
                :updatedAt ,timestamp))
             (result
              (jsonrpc-request
               connection "notes/upsert"
               `(:session ,groket-session-reference
                 :expectedRevision ,groket-notes-revision
                 :note ,note)
               :timeout groket-request-timeout)))
        (setq groket-notes-revision (plist-get result :revision))
        (groket-refresh)))))

(defun groket-delete-note (&optional no-confirm)
  "Delete the note at point, asking first unless NO-CONFIRM is non-nil."
  (interactive)
  (let ((note-id (groket--ancestor-property "GROKET_NOTE_ID")))
    (unless note-id (user-error "Point is not inside an operator note"))
    (when (or no-confirm (yes-or-no-p (format "Delete note %s? " note-id)))
      (let ((result
             (jsonrpc-request
              (groket-connect) "notes/delete"
              `(:session ,groket-session-reference
                :expectedRevision ,groket-notes-revision
                :noteId ,note-id)
              :timeout groket-request-timeout)))
        (setq groket-notes-revision (plist-get result :revision))
        (groket-refresh)))))

(defun groket-save-buffer ()
  "Save every operator note in the current session buffer."
  (interactive)
  (let (positions)
    (goto-char (point-min))
    (org-map-entries
     (lambda ()
       (when (org-entry-get nil "GROKET_NOTE_ID" nil)
         (push (copy-marker (point)) positions)))
     nil nil)
    (dolist (position (nreverse positions))
      (goto-char position)
      (groket-save-note))
    (set-buffer-modified-p nil))
  t)

(defvar-keymap groket-session-mode-map
  :parent org-mode-map
  "g" #'groket-refresh
  "C-c C-n" #'groket-new-note
  "C-c C-k" #'groket-delete-note
  "C-c C-o" #'groket-open-prompt-at-point
  "C-c C-c" #'groket-save-note
  "C-x C-s" #'groket-save-buffer)

(define-derived-mode groket-session-mode org-mode "Groket"
  "Major mode for live Groket Org session buffers."
  (setq-local write-contents-functions '(groket-save-buffer))
  (setq-local mode-line-process
              '(:eval
                (concat
                 (when groket-session-stale " Trace changed")
                 (when groket-notes-stale " Notes changed")))))

(provide 'groket)
;;; groket.el ends here
