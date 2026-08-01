;;; groket-tests.el --- Tests for Groket Org integration -*- lexical-binding: t; -*-

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

(ert-deftest groket-new-note-uses-schema-and-current-turn ()
  (with-temp-buffer
    (groket-session-mode)
    (groket--apply-document groket-test--document "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "inspect this")
    (let (saved-note)
      (cl-letf (((symbol-function 'groket-connect) (lambda () 'connection))
                ((symbol-function 'groket-refresh) #'ignore)
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
                ((symbol-function 'groket-refresh) #'ignore)
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method params &rest _keys)
                   (should (equal method "notes/delete"))
                   (setq sent-params params)
                   '(:revision "rev-2"))))
        (groket-delete-note t))
      (should (equal (plist-get sent-params :session) "session-emacs"))
      (should (equal (plist-get sent-params :expectedRevision) "rev-1"))
      (should (equal (plist-get sent-params :noteId) "n-emacs")))))

(ert-deftest groket-notifications-target-the-matching-session ()
  (let ((matching (generate-new-buffer " *groket-matching*"))
        (other (generate-new-buffer " *groket-other*")))
    (unwind-protect
        (progn
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

(provide 'groket-tests)
;;; groket-tests.el ends here
