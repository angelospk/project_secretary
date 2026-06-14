"""Backlog Q&A (subsystem #8): retrieval over the memory, optional LLM synthesis.

Read-only by construction. `retrieve.query` is useful without any LLM; `synth.answer`
layers a grounded, cited answer on top when a model is configured.
"""
