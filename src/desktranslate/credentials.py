from __future__ import annotations

import ctypes
from ctypes import wintypes


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class CredentialStore:
    """Windows 凭据管理器封装。

    每条 API 配置使用独立的凭据目标（``DeskTranslate/DeepSeek/<id>``），因此可以
    同时保存多把密钥；旧版本只写过一个固定目标，保留 ``TARGET`` 供迁移使用。
    """

    TARGET = "DeskTranslate/DeepSeek"

    def __init__(self) -> None:
        self._api = ctypes.WinDLL("Advapi32", use_last_error=True)
        pointer = ctypes.POINTER(_Credential)
        self._api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(pointer)]
        self._api.CredReadW.restype = wintypes.BOOL
        self._api.CredWriteW.argtypes = [pointer, wintypes.DWORD]
        self._api.CredWriteW.restype = wintypes.BOOL
        self._api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._api.CredDeleteW.restype = wintypes.BOOL
        self._api.CredFree.argtypes = [ctypes.c_void_p]
        self._api.CredFree.restype = None

    @staticmethod
    def _target(target: str | None) -> str:
        return target or CredentialStore.TARGET

    def read(self, target: str | None = None) -> str | None:
        credential = ctypes.POINTER(_Credential)()
        if not self._api.CredReadW(self._target(target), 1, 0, ctypes.byref(credential)):
            code = ctypes.get_last_error()
            if code == 1168:
                return None
            raise ctypes.WinError(code)
        try:
            value = credential.contents
            return ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize).decode("utf-8")
        finally:
            self._api.CredFree(credential)

    def save(self, key: str, target: str | None = None) -> None:
        encoded = key.encode("utf-8")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = _Credential(
            Type=1,
            TargetName=self._target(target),
            CredentialBlobSize=len(encoded),
            CredentialBlob=blob,
            Persist=2,
            UserName="DeskTranslate",
        )
        if not self._api.CredWriteW(ctypes.byref(credential), 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def delete(self, target: str | None = None) -> None:
        if not self._api.CredDeleteW(self._target(target), 1, 0):
            code = ctypes.get_last_error()
            if code != 1168:
                raise ctypes.WinError(code)