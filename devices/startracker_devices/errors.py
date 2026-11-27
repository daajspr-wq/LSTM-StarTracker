"""Exceptions de la bibliothèque, et traduction des erreurs alpyca / réseau."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import requests
from alpaca import exceptions as ax


class DeviceError(Exception):
    """Erreur de base. `detail` contient le message d'origine (driver, réseau...)."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail


class DeviceNotFound(DeviceError):
    """Identifiant inconnu, ou mauvais type d'appareil."""


class DeviceNotReady(DeviceError):
    """Non connecté, monture parquée, suivi arrêté, opération déjà en cours..."""


class NotSupported(DeviceError):
    """L'appareil ne sait pas faire ça (voir les champs can_* de info())."""


class DeviceUnavailable(DeviceError):
    """Serveur Alpaca injoignable (programme fermé, réseau, câble)."""


class DeviceTimeout(DeviceError):
    """L'appareil n'a pas répondu ou n'a pas fini à temps."""


class OperationAborted(DeviceError):
    """Opération interrompue (stop_slew, abort...)."""


class InvalidParameter(DeviceError):
    """Paramètre hors limites ou refusé par l'appareil."""


class DeviceInternalError(DeviceError):
    """Erreur du driver non classée : voir `detail`, à journaliser."""


@contextmanager
def translate() -> Iterator[None]:
    """Convertit les exceptions alpyca et requests en exceptions de la bibliothèque."""
    try:
        yield
    except DeviceError:
        raise
    except (ax.NotConnectedException, ax.ParkedException, ax.SlavedException,
            ax.InvalidOperationException, ax.ValueNotSetException) as e:
        raise DeviceNotReady(_msg(e)) from e
    except ax.InvalidValueException as e:
        raise InvalidParameter(_msg(e)) from e
    except (ax.NotImplementedException, ax.ActionNotImplementedException) as e:
        raise NotSupported(_msg(e)) from e
    except ax.OperationCancelledException as e:
        raise OperationAborted(_msg(e)) from e
    except ax.AlpacaRequestException as e:
        # Erreurs HTTP : 400 = paramètre ou numéro d'appareil invalide.
        status = e.number if hasattr(e, "number") else None
        if status == 400:
            raise InvalidParameter(_msg(e)) from e
        raise DeviceInternalError(_msg(e)) from e
    except ax.DriverException as e:
        raise DeviceInternalError(_msg(e)) from e
    except requests.Timeout as e:
        raise DeviceTimeout(str(e)) from e
    except requests.ConnectionError as e:
        raise DeviceUnavailable(str(e)) from e


def _msg(e: Exception) -> str:
    return str(getattr(e, "message", "") or e)
