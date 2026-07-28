from llama_cpp import Llama

_llm = None
_config = None


def init(model_path: str, config: dict):
    global _llm, _config
    _config = config
    print(f"🦙 Lade Llama Core-Modell: {model_path}")

    _llm = Llama(
        model_path=model_path,
        n_gpu_layers=-1,
        n_ctx=4096,
        n_threads=4,
        verbose=True
    )
    print("✅ Llama Core erfolgreich im VRAM geladen!")


def get_response(chat_history: list, temperature: float | None = None) -> str | None:
    try:
        _llm.reset()

        response = _llm.create_chat_completion(
            messages=chat_history,
            max_tokens=_config["max_tokens"],
            temperature=temperature if temperature is not None else 0.8,
            top_p=0.95,
            repeat_penalty=1.2,
            frequency_penalty=0.5
        )
        return response["choices"][0]["message"]["content"].strip()

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ [Llama-Fehler]: {e}")
        return None