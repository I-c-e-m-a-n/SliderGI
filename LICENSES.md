# Licences and attribution

Original GI-Coder wrapper and tests: MIT, see [LICENSE](LICENSE). Third-party
projects retain their own licences. Weights and upstream binaries are not committed.

* **llama.cpp**, ggml authors: MIT. Pinned official source commit
  `74d4f5b041ad837153b0e90fc864b8290e01d8d5` (`b5046`). Setup retains its full
  checkout, including `LICENSE` and vendored dependency notices. Binary
  redistribution must include applicable notices. [Official licence](https://github.com/ggml-org/llama.cpp/blob/74d4f5b041ad837153b0e90fc864b8290e01d8d5/LICENSE).
* **Qwen2.5-Coder**, Qwen team: the model-specific licence files and cards govern
  weights, not GI-Coder's MIT licence. Direct inspection in this development
  cloud was blocked by network policy. Review the official repositories before
  use/redistribution: [7B](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/tree/main),
  [3B](https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct-GGUF/tree/main).
* **huggingface_hub**: Apache-2.0, <https://github.com/huggingface/huggingface_hub>.
  Installed dependencies retain their licence metadata inside `.venv`.
* **Python**: PSF licence, <https://docs.python.org/3/license.html>.

Model SHA-256 verification establishes integrity, not licence permission.
