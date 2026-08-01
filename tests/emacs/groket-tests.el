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

(provide 'groket-tests)
;;; groket-tests.el ends here
