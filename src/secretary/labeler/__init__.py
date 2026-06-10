"""Subsystem #5 — the labeler: seeded thematic classification of issues.

Classifies issues into a maintainer-owned taxonomy (not unsupervised clusters) and,
with staged trust, suggests or applies the labels. The pure core (taxonomy, centroids,
classification bands, veto rules) is separated from the DB/GitHub I/O in `apply`.
"""
