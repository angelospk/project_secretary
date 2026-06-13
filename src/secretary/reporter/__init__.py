"""The Reporter (subsystem #10): maintainer digest + release-notes drafts.

Pure read path over data the secretary already computes (sync records, plan drift,
gardener findings, organizer themes). The only writes are the usual managed digest
section and an optional Discord webhook POST. Publishing a release stays the human's job.
"""
