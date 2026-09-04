"""Offline training and evaluation.

Everything in this package needs scikit-learn and NumPy and runs in the
development environment only. Nothing under `mandateguard.discovery` or
`mandateguard.product` imports it, so the public runtime image never installs
these dependencies. See `requirements-train.txt`.
"""
