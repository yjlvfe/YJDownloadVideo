#!/bin/sh
# WPC can exceed the memory/time budget on the current Render instance.
# Fail fast so the bounded CDP fallback handles YouTube without leaving jobs stuck.
exit 1
