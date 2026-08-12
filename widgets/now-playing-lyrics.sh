#!/bin/zsh

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# `widgets/lyrics/` is a package (it uses relative imports internally), and
# only `-m` gives a run of it real package context -- see
# widgets/lyrics/__main__.py. So this points PYTHONPATH at its parent rather
# than executing a file inside it directly.
export PYTHONPATH="${0:A:h}${PYTHONPATH:+:$PYTHONPATH}"

exec /usr/bin/env python3 -m lyrics "$@"
