# idat-sync

idat-sync automatiza la descarga y organización del material de tus cursos (Moodle, SharePoint/OneDrive) y las grabaciones de clase. Está pensado para evitar la tarea tediosa de abrir muchos enlaces, crear carpetas manualmente y perder tiempo buscando archivos dispersos.

## ¿Qué hace?

- Descarga y organiza automáticamente los archivos por curso y módulos.
- Descarga grabaciones de video (cuando aun están accesibles) y las guarda en la estructura del curso.
- Interfaz gráfica simple para seleccionar carpeta de sincronización y ver el progreso.

## Requisitos

- Python 3.10+

## Instalación

1.  **Instalar el paquete:**
    ```shell
    pip install git+https://github.com/tony-97/idat-sync
    ```
2.  **Instalar el navegador para Playwright (solo en Linux / macOS):**
    ```shell
    playwright install chromium
    ```

## Cómo usar

1. Una vez instalado, simplemente ejecuta el siguiente comando en tu terminal para abrir la aplicación:
   ```shell
   idat_sync
   ```
2. En la ventana:

   - Ingresa tu codigo de alumno y contraseña.

     ![Login](screenshots/login.png)

   - Completa la autenticación de dos factores (2FA) con el código recibido en tu dispositivo móvil.
   - Una vez que cargue la ventana principal, selecciona la carpeta de destino para tus archivos.
   - Haz clic en el botón **"Sync"** para iniciar el proceso. Podrás ver el progreso en la misma ventana.
     ![Progress](screenshots/sync_progress.png)

3. Los archivos se descargarán y organizarán en la carpeta que seleccionaste.

## Capturas de pantalla

### Estructura del material sincronizado

![Lista de cursos](screenshots/result_folder.png)

![Estructura del material sincronizado](screenshots/result_folder3.png) ![Estructura del material sincronizado](screenshots/result_folder2.png)

## TODO

- [ ] Archivo de configuración usando la libreria platformdirs.
- [ ] Versión mobil.
- [ ] Opción para seleccionar cursos específicos para sincronizar.
- [ ] Descargar los contenidos html en pdf.
