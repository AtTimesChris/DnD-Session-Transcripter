"""Store the Hugging Face token only in Windows Credential Manager."""
SERVICE = "LocalSessionKeeper"
USERNAME = "huggingface_read_token"


def _windows_keyring():
    import keyring
    backend = keyring.get_keyring()
    if backend.__class__.__name__ != "WinVaultKeyring":
        raise RuntimeError(
            "The active keyring backend is not Windows Credential Manager; refusing to store the token in another backend."
        )
    return keyring


def get_huggingface_token():
    return _windows_keyring().get_password(SERVICE, USERNAME) or ""


def set_huggingface_token(token):
    _windows_keyring().set_password(SERVICE, USERNAME, token)
