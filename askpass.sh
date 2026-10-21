#!/bin/sh
# SSH_ASKPASS helper used by import_submissions.py: answers the SSH key passphrase prompt
# with $SSH_KEY_PASSPHRASE (set in batch.env).
printf '%s\n' "${SSH_KEY_PASSPHRASE}"
