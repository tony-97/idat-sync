import os
from idat_sync.idat_sync import IDATSync
from idat_sync.auth import AuthProvider

auth = AuthProvider()
idat_sync = IDATSync(auth.get_credentials())  # type: ignore
os.makedirs("./recordings", exist_ok=True)
idat_sync.download_recordings(
    "II.01.2025-III EFSRT1: PROYECTO DESARROLLO DE LOS COMPONENTES DE LA CAPA DE VISTA",
    "./recordings",
)
