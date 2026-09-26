;;; groket-tests.el --- Tests for Groket Org integration -*- lexical-binding: t; -*-

;;; Commentary:

;; Run from the repository root:
;;
;;   emacs --batch -L groket/integrations/emacs -l ert \
;;         -l tests/emacs/groket-tests.el -f ert-run-tests-batch-and-exit
;;
;; Only ert, org and jsonrpc are needed; control requests are stubbed.

;;; Code:

(require 'cl-lib)
(require 'ert)
(require 'org)
(require 'groket)

(defconst groket-test--document
  "#+TITLE: Socket review
#+PROPERTY: GROKET_SESSION_ID session-emacs
#+PROPERTY: GROKET_NOTES_REVISION rev-1

* Prompt 6
:PROPERTIES:
:GROKET_PROMPT_INDEX: 6
:GROKET_TURN_INDEX: 0
:END:

** User

: inspect this

** Operator notes

*** Original summary
:PROPERTIES:
:GROKET_NOTE_ID: n-emacs
:GROKET_EVENT_INDICES: 1,2
:GROKET_CREATED_AT: 2026-08-01T12:00:00+00:00
:GROKET_UPDATED_AT: 2026-08-01T12:00:00+00:00
:END:

**** Summary
:PROPERTIES:
:GROKET_FIELD_ID: summary
:END:
Original summary

**** Detail
:PROPERTIES:
:GROKET_FIELD_ID: detail
:END:
Original detail
")

(defconst groket-test--two-note-document
  "#+TITLE: Socket review
#+PROPERTY: GROKET_SESSION_ID session-emacs
#+PROPERTY: GROKET_NOTES_REVISION rev-1

* Prompt 6
:PROPERTIES:
:GROKET_PROMPT_INDEX: 6
:GROKET_TURN_INDEX: 0
:END:

** User

: inspect this

** Operator notes

*** First
:PROPERTIES:
:GROKET_NOTE_ID: n-first
:GROKET_EVENT_INDICES: 1
:GROKET_CREATED_AT: 2026-08-01T12:00:00+00:00
:GROKET_UPDATED_AT: 2026-08-01T12:00:00+00:00
:END:

**** Summary
:PROPERTIES:
:GROKET_FIELD_ID: summary
:END:
: First summary

**** Detail
:PROPERTIES:
:GROKET_FIELD_ID: detail
:END:
: First detail

*** Second
:PROPERTIES:
:GROKET_NOTE_ID: n-second
:GROKET_EVENT_INDICES: 2
:GROKET_CREATED_AT: 2026-08-01T12:05:00+00:00
:GROKET_UPDATED_AT: 2026-08-01T12:05:00+00:00
:END:

**** Summary
:PROPERTIES:
:GROKET_FIELD_ID: summary
:END:
: Second summary

**** Detail
:PROPERTIES:
:GROKET_FIELD_ID: detail
:END:
: Second detail
")

(defun groket-test--render (&optional text)
  "Render TEXT (or the two-note document) into the current buffer."
  (groket-session-mode)
  (groket--apply-document (or text groket-test--two-note-document)
                          "session-emacs" "rev-1" "session-emacs"))

(defun groket-test--append (search text)
  "Type TEXT at the end of the field body containing SEARCH."
  (goto-char (point-min))
  (search-forward search)
  (insert text))

(defun groket-test--render-result (&optional text)
  "Return a `session/render' result carrying TEXT."
  (list :text (or text groket-test--two-note-document)
        :sessionId "session-emacs"
        :notesRevision "rev-1"))


;;; Projection and read-only regions

(ert-deftest groket-document-protects-trace-and-opens-note-fields ()
  (with-temp-buffer
    (groket-session-mode)
    (groket--apply-document groket-test--document "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "inspect this")
    (should (get-text-property (point) 'read-only))
    (should-error (insert "x") :type 'text-read-only)
    (goto-char (point-min))
    (search-forward "Original detail")
    (should-not (get-text-property (1- (point)) 'read-only))
    (insert " amended")
    (should (string-match-p "Original detail amended" (buffer-string)))))

(ert-deftest groket-field-body-stops-before-the-blank-separator ()
  "Structure typed at column 0 must not land inside a field body."
  (with-temp-buffer
    (groket-test--render)
    (goto-char (point-min))
    (search-forward ": First summary")
    (insert " tail")
    (should (string-match-p ": First summary tail" (buffer-string)))
    (forward-line 1)
    (beginning-of-line)
    (should (looking-at-p "^$"))
    (should (get-text-property (point) 'read-only))
    (should-error (insert "*** fabricated") :type 'text-read-only)))

(ert-deftest groket-document-parses-note-at-point ()
  (with-temp-buffer
    (groket-session-mode)
    (groket--apply-document groket-test--document "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "Original detail")
    (let* ((note (groket--note-at-point))
           (fields (plist-get note :fields)))
      (should (equal (plist-get note :id) "n-emacs"))
      (should (= (plist-get note :turnIndex) 0))
      (should (equal (plist-get note :eventIndices) [1 2]))
      (should (equal (gethash "summary" fields) "Original summary"))
      (should (equal (gethash "detail" fields) "Original detail")))))

(ert-deftest groket-field-value-keeps-leading-and-trailing-blank-lines ()
  "Blank lines inside a fixed-width field body survive save-parse."
  (with-temp-buffer
    (groket-session-mode)
    (groket--apply-document
     "#+TITLE: blanks
#+PROPERTY: GROKET_SESSION_ID session-emacs
#+PROPERTY: GROKET_NOTES_REVISION rev-1

* Prompt 1
:PROPERTIES:
:GROKET_PROMPT_INDEX: 1
:GROKET_TURN_INDEX: 0
:END:

** Operator notes

*** Note
:PROPERTIES:
:GROKET_NOTE_ID: n-blank
:GROKET_EVENT_INDICES: 1
:GROKET_CREATED_AT: 2026-08-01T12:00:00+00:00
:GROKET_UPDATED_AT: 2026-08-01T12:00:00+00:00
:END:

**** Summary
:PROPERTIES:
:GROKET_FIELD_ID: summary
:END:
:
: alpha
:

**** Detail
:PROPERTIES:
:GROKET_FIELD_ID: detail
:END:
:
: text
"
     "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "alpha")
    (let* ((note (groket--note-at-point))
           (fields (plist-get note :fields)))
      (should (equal (gethash "summary" fields) "\nalpha\n"))
      (should (equal (gethash "detail" fields) "\ntext")))))

(ert-deftest groket-document-records-rendered-note-ids ()
  (with-temp-buffer
    (groket-test--render)
    (should (equal groket--rendered-note-ids '("n-first" "n-second")))
    (should (groket--rendered-note-id-p "n-first"))
    (should-not (groket--rendered-note-id-p "n-typed"))))

(ert-deftest groket-document-finds-source-prompt-index ()
  (with-temp-buffer
    (groket-session-mode)
    (groket--apply-document groket-test--document "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "inspect this")
    (should (= (groket--prompt-index-at-point) 6))))

(ert-deftest groket-session-reference-preserves-catalog-ids ()
  (should (equal (groket--normalize-session-reference "session-emacs")
                 "session-emacs"))
  (let ((directory (make-temp-file "groket-session-" t)))
    (unwind-protect
        (should (equal (groket--normalize-session-reference directory)
                       (file-truename directory)))
      (delete-directory directory))))


;;; Saving

(ert-deftest groket-save-note-keeps-other-unsaved-edits-visible ()
  "One saved note must not advertise the whole buffer as saved."
  (with-temp-buffer
    (groket-test--render)
    (groket-test--append ": First detail" " one")
    (groket-test--append ": Second detail" " two")
    (should (buffer-modified-p))
    (let (sent)
      (cl-letf (((symbol-function 'groket-connect) (lambda () 'connection))
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method params &rest _keys)
                   (should (equal method "notes/upsert"))
                   (push (plist-get params :note) sent)
                   '(:revision "rev-2"))))
        (goto-char (point-min))
        (search-forward ": First detail")
        (groket-save-note))
      (should (= (length sent) 1))
      (should (equal (plist-get (car sent) :id) "n-first"))
      (should (equal (gethash "detail" (plist-get (car sent) :fields))
                     "First detail one"))
      (should (buffer-modified-p)))))

(ert-deftest groket-save-buffer-saves-every-note-and-clears-modified ()
  (with-temp-buffer
    (groket-test--render)
    (groket-test--append ": First detail" " one")
    (groket-test--append ": Second detail" " two")
    (goto-char (point-min))
    (search-forward ": Second summary")
    (let ((point-before (point))
          ids)
      (cl-letf (((symbol-function 'groket-connect) (lambda () 'connection))
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection _method params &rest _keys)
                   (push (plist-get (plist-get params :note) :id) ids)
                   '(:revision "rev-2"))))
        (groket-save-buffer))
      (should (equal (sort ids #'string<) '("n-first" "n-second")))
      (should-not (buffer-modified-p))
      (should (= (point) point-before)))))

(ert-deftest groket-save-buffer-skips-notes-outside-the-projection ()
  "A note id typed into the buffer names nothing on the server."
  (with-temp-buffer
    (groket-test--render)
    (let ((inhibit-read-only t))
      (goto-char (point-max))
      (insert "\n*** Fabricated\n:PROPERTIES:\n:GROKET_NOTE_ID: n-typed\n:END:\n\n"
              "**** Summary\n:PROPERTIES:\n:GROKET_FIELD_ID: summary\n:END:\n: typed\n"))
    (let (ids)
      (cl-letf (((symbol-function 'groket-connect) (lambda () 'connection))
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection _method params &rest _keys)
                   (push (plist-get (plist-get params :note) :id) ids)
                   '(:revision "rev-2"))))
        (groket-save-buffer))
      (should (equal (sort ids #'string<) '("n-first" "n-second")))
      (should-not (member "n-typed" ids)))))

(ert-deftest groket-save-note-refuses-an-unrendered-note ()
  (with-temp-buffer
    (groket-test--render)
    (let ((inhibit-read-only t))
      (goto-char (point-max))
      (insert "\n*** Fabricated\n:PROPERTIES:\n:GROKET_NOTE_ID: n-typed\n:END:\n\n"
              "**** Summary\n:PROPERTIES:\n:GROKET_FIELD_ID: summary\n:END:\n: typed\n"))
    (let (requests)
      (cl-letf (((symbol-function 'groket-connect) (lambda () 'connection))
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method &rest _keys)
                   (push method requests)
                   '(:revision "rev-2"))))
        (goto-char (point-min))
        (search-forward ": typed")
        (should-error (groket-save-note) :type 'user-error))
      (should-not requests))))


;;; Mutations

(ert-deftest groket-new-note-uses-schema-and-current-turn ()
  (with-temp-buffer
    (groket-session-mode)
    (groket--apply-document groket-test--document "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "inspect this")
    (let (saved-note)
      (cl-letf (((symbol-function 'groket-connect) (lambda () 'connection))
                ((symbol-function 'groket--do-refresh) #'ignore)
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method params &rest _keys)
                   (pcase method
                     ("notes/list"
                      '(:schema (:fields [(:id "summary") (:id "detail")])
                        :revision "rev-1"))
                     ("notes/upsert"
                      (setq saved-note (plist-get params :note))
                      '(:revision "rev-2"))))))
        (groket-new-note))
      (should (= (plist-get saved-note :turnIndex) 0))
      (should (string-prefix-p "n-" (plist-get saved-note :id)))
      (should (equal (gethash "summary" (plist-get saved-note :fields)) ""))
      (should (equal (gethash "detail" (plist-get saved-note :fields)) "")))))

(ert-deftest groket-delete-note-sends-revision-safe-request ()
  (with-temp-buffer
    (groket-session-mode)
    (groket--apply-document groket-test--document "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "Original detail")
    (let (sent-params)
      (cl-letf (((symbol-function 'groket-connect) (lambda () 'connection))
                ((symbol-function 'groket--do-refresh) #'ignore)
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method params &rest _keys)
                   (should (equal method "notes/delete"))
                   (setq sent-params params)
                   '(:revision "rev-2"))))
        (groket-delete-note t))
      (should (equal (plist-get sent-params :session) "session-emacs"))
      (should (equal (plist-get sent-params :expectedRevision) "rev-1"))
      (should (equal (plist-get sent-params :noteId) "n-emacs")))))

(ert-deftest groket-new-note-refuses-before-touching-the-server ()
  "A mutation the buffer cannot reload afterwards must not reach the server."
  (with-temp-buffer
    (groket-test--render)
    (groket-test--append ": First detail" " pending")
    (let (requests)
      (cl-letf (((symbol-function 'groket-connect) (lambda () 'connection))
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method &rest _keys)
                   (push method requests)
                   '(:revision "rev-2"))))
        (should-error (groket-new-note) :type 'user-error))
      (should-not requests)
      (should (string-match-p "First detail pending" (buffer-string))))))

(ert-deftest groket-delete-note-refuses-before-touching-the-server ()
  (with-temp-buffer
    (groket-test--render)
    (groket-test--append ": First detail" " pending")
    (let (requests)
      (cl-letf (((symbol-function 'groket-connect) (lambda () 'connection))
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method &rest _keys)
                   (push method requests)
                   '(:revision "rev-2"))))
        (should-error (groket-delete-note t) :type 'user-error))
      (should-not requests))))


;;; Refresh and notifications

(ert-deftest groket-refresh-keeps-flags-raised-during-the-request ()
  "A notification landing mid-render describes drift the response lacks."
  (with-temp-buffer
    (groket-test--render)
    (cl-letf (((symbol-function 'groket--render-session)
               (lambda (_session)
                 (setq groket-notes-stale t)
                 (groket-test--render-result))))
      (groket--do-refresh))
    (should groket-notes-stale)
    (should-not groket-session-stale)))

(ert-deftest groket-refresh-clears-flags-the-response-covers ()
  (with-temp-buffer
    (groket-test--render)
    (setq groket-notes-stale t
          groket-session-stale t)
    (cl-letf (((symbol-function 'groket--render-session)
               (lambda (_session) (groket-test--render-result))))
      (groket--do-refresh))
    (should-not groket-notes-stale)
    (should-not groket-session-stale)))

(ert-deftest groket-auto-refresh-skips-buffers-with-unsaved-edits ()
  (with-temp-buffer
    (groket-test--render)
    (groket-test--append ": First detail" " pending")
    (let (refreshed)
      (cl-letf (((symbol-function 'groket--do-refresh)
                 (lambda () (setq refreshed t))))
        (groket--notification
         nil 'notes/changed '(:sessionId "session-emacs" :revision "rev-9")))
      (should-not refreshed)
      (should groket-notes-stale)
      (should (buffer-modified-p))
      (should (string-match-p "First detail pending" (buffer-string))))))

(ert-deftest groket-auto-refresh-reloads-a-clean-buffer ()
  (with-temp-buffer
    (groket-test--render)
    (let (refreshed)
      (cl-letf (((symbol-function 'groket--do-refresh)
                 (lambda () (setq refreshed t))))
        (groket--notification
         nil 'notes/changed '(:sessionId "session-emacs" :revision "rev-9")))
      (should refreshed))))

(ert-deftest groket-notifications-target-the-matching-session ()
  (let ((matching (generate-new-buffer " *groket-matching*"))
        (other (generate-new-buffer " *groket-other*")))
    (unwind-protect
        (cl-letf (((symbol-function 'groket--do-refresh) #'ignore))
          (with-current-buffer matching
            (groket-session-mode)
            (groket--apply-document
             groket-test--document "session-emacs" "rev-1" "session-emacs"))
          (with-current-buffer other
            (groket-session-mode)
            (groket--apply-document
             groket-test--document "session-other" "rev-1" "session-other"))
          (groket--notification
           nil 'notes/changed '(:sessionId "session-emacs" :revision "rev-2"))
          (groket--notification
           nil 'session/changed '(:sessionId "session-emacs"))
          (with-current-buffer matching
            (should groket-notes-stale)
            (should groket-session-stale))
          (with-current-buffer other
            (should-not groket-notes-stale)
            (should-not groket-session-stale)))
      (kill-buffer matching)
      (kill-buffer other))))


;;; Opening sessions

(ert-deftest groket-open-session-keeps-unsaved-edits-when-refused ()
  (let ((buffer (get-buffer-create "*groket:session-emacs*"))
        asked opened)
    (unwind-protect
        (progn
          (with-current-buffer buffer
            (groket-test--render)
            (groket-test--append ": First detail" " pending"))
          (cl-letf (((symbol-function 'groket--connection-for-session)
                     (lambda (_session) 'connection))
                    ((symbol-function 'pop-to-buffer) (lambda (target &rest _) target))
                    ((symbol-function 'yes-or-no-p)
                     (lambda (_prompt) (setq asked t) nil))
                    ((symbol-function 'jsonrpc-request)
                     (lambda (_connection method &rest _keys)
                       (pcase method
                         ("session/render" (groket-test--render-result))
                         ("session/open" (setq opened t) nil)))))
            (groket-open-session "session-emacs"))
          (should asked)
          (should opened)
          (with-current-buffer buffer
            (should (string-match-p "First detail pending" (buffer-string)))
            (should (buffer-modified-p))))
      (kill-buffer buffer))))

(ert-deftest groket-open-session-re-renders-when-discard-is-confirmed ()
  (let ((buffer (get-buffer-create "*groket:session-emacs*")))
    (unwind-protect
        (progn
          (with-current-buffer buffer
            (groket-test--render)
            (groket-test--append ": First detail" " pending"))
          (cl-letf (((symbol-function 'groket--connection-for-session)
                     (lambda (_session) 'connection))
                    ((symbol-function 'pop-to-buffer) (lambda (target &rest _) target))
                    ((symbol-function 'yes-or-no-p) (lambda (_prompt) t))
                    ((symbol-function 'jsonrpc-request)
                     (lambda (_connection method &rest _keys)
                       (pcase method
                         ("session/render" (groket-test--render-result))
                         ("session/open" nil)))))
            (groket-open-session "session-emacs"))
          (with-current-buffer buffer
            (should-not (string-match-p "First detail pending" (buffer-string)))
            (should-not (buffer-modified-p))))
      (kill-buffer buffer))))

(ert-deftest groket-session-entry-annotation-includes-status-and-model ()
  (let ((entry '(:sessionId "alpha-1"
                 :title "Socket review"
                 :status "complete"
                 :model "grok-4"
                 :origin "work")))
    (should (string-match-p "Socket review" (groket--session-entry-annotation entry)))
    (should (string-match-p "complete" (groket--session-entry-annotation entry)))
    (should (string-match-p "grok-4" (groket--session-entry-annotation entry)))
    (should (equal (groket--session-entry-path entry) "alpha-1"))))

(ert-deftest groket-session-entry-path-prefers-path ()
  (should (equal (groket--session-entry-path
                  '(:sessionId "alpha" :path "/tmp/alpha"))
                 "/tmp/alpha"))
  (should (equal (groket--session-entry-path '(:sessionId "alpha"))
                 "alpha")))


;;; Connection lifecycle

(ert-deftest groket-request-drops-a-dead-connection ()
  (let ((groket--connection 'connection))
    (cl-letf (((symbol-function 'jsonrpc-running-p) (lambda (_connection) nil))
              ((symbol-function 'jsonrpc-request)
               (lambda (&rest _) (error "Peer gone"))))
      (should-error (groket--request 'connection "session/render" nil))
      (should-not groket--connection))))

(ert-deftest groket-request-keeps-a-live-connection ()
  (let ((groket--connection 'connection))
    (cl-letf (((symbol-function 'jsonrpc-running-p) (lambda (_connection) t))
              ((symbol-function 'jsonrpc-request)
               (lambda (&rest _) (error "Request timed out"))))
      (should-error (groket--request 'connection "session/render" nil))
      (should (eq groket--connection 'connection)))))

(ert-deftest groket-killing-the-last-session-buffer-drops-the-connection ()
  (let ((first (generate-new-buffer " *groket-one*"))
        (second (generate-new-buffer " *groket-two*"))
        (groket--connection 'connection)
        (shutdowns 0))
    (cl-letf (((symbol-function 'jsonrpc-shutdown)
               (lambda (_connection) (setq shutdowns (1+ shutdowns)))))
      (with-current-buffer first (groket-session-mode))
      (with-current-buffer second (groket-session-mode))
      (kill-buffer first)
      (should (eq groket--connection 'connection))
      (should (= shutdowns 0))
      (kill-buffer second)
      (should-not groket--connection)
      (should (= shutdowns 1)))))

(ert-deftest groket-connection-for-session-restarts-a-stale-socket ()
  "A socket file outliving its TUI must not block reconnection forever."
  (let* ((directory (make-temp-file "groket-session-" t))
         (socket (expand-file-name "control.sock" directory))
         (groket--connection nil)
         (starts 0)
         (attempts 0))
    (unwind-protect
        (cl-letf (((symbol-function 'groket-connected-p) (lambda () nil))
                  ((symbol-function 'groket--socket-path) (lambda () socket))
                  ((symbol-function 'groket-start)
                   (lambda (&rest _) (setq starts (1+ starts))))
                  ((symbol-function 'groket-connect)
                   (lambda ()
                     (setq attempts (1+ attempts))
                     (when (= attempts 1)
                       (signal 'file-error
                               '("make client process failed" "Connection refused")))
                     'connection)))
          (write-region "" nil socket nil 'silent)
          (groket--connection-for-session directory)
          (should (= starts 1))
          (should (= attempts 2)))
      (delete-directory directory t))))

(ert-deftest groket-evil-binding-survives-byte-compilation ()
  "The Evil binding must call a function; a macro breaks in compiled code."
  (let* ((source (concat (file-name-sans-extension (locate-library "groket")) ".el"))
         (compiled (make-temp-file "groket-compiled" nil ".elc"))
         (byte-compile-dest-file-function (lambda (_source) compiled))
         (byte-compile-warnings nil)
         bindings)
    (unwind-protect
        (progn
          (should (byte-compile-file source))
          (cl-letf (((symbol-function 'evil-define-key)
                     (cons 'macro
                           (lambda (&rest _)
                             (error "Macro form reached at run time"))))
                    ((symbol-function 'evil-define-key*)
                     (lambda (state _keymap key definition)
                       (push (list state key definition) bindings))))
            (load compiled nil t)
            (provide 'evil))
          (should (cl-find #'groket-refresh bindings :key #'cl-third)))
      (delete-file compiled))))

(provide 'groket-tests)
;;; groket-tests.el ends here
