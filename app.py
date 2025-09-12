# app.py
import os
import zipfile
from flask import Flask, request, send_file, render_template_string
from werkzeug.utils import secure_filename
from Decimal import Decimal
from tempfile import TemporaryDirectory

# Importamos tu función run_pipeline desde procesedor.py
from procesedor import run_pipeline

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB maxupload
app.config['SECRET_KEY'] = 'clave_secretaaparaflask_2025'

HTML_FORM = '''
<!doctype html>
<title>Procesador de XML para DetraccionesSUNAT</title>
<linkhref="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
<div class="container mt-5">
    <h1 class="mb-4">Procesador deXML/ZIP → TXTDetracciones</h1>
    <form id="uploadForm" method="post" enctype="multipart/form-data" class="mb-4">
        <div class="mb-3">
            <label for="files" class="form-label">Selecciona tus archivosXML oZIP:</label>
            <input class="form-control" type="file" name="files" id="files" multiplerequired accept=".xml,.zip" title="Solo XML y ZIP files are allowed">
            <div id="fileCount" class="form-text text-muted mt-1">Ningúnarchivoseleccionado.</div>
        </div>
        <div class="d-flex gap-2">
            <button type="submit" class="btnbtn-primary"disabled>Procesar yDescargarResultados (TXT + CSV)</button>
            <button type="button" class="btnbtn-outline-secondary" id="clearButton">Borrar selección</button>
        </div>
    </form>
    <div class="alert alert-info">
        <strong>Nota:</strong>Este sistema procesasa archivosXML de facturas electrónicas y genera el archivo .txt para pago masivo de detracciones en SUNAT, junto con un reporte detallado de omitidos en .csv.
    </div>
</div>

<script>
document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('uploadForm');
    const filesInput = document.getElementById('files');
    const fileCountDiv = document.getElementById('fileCount');
    const submitButton = form.querySelector('button[type="submit"]');
    const clearButton = document.getElementById('clearButton');

    // Habilitar/deshabilitar botón y mostrar conte
    filesInput.addEventListener('change', function() {
        if (filesInput.files.length > 0) {
            // Verificar que todos los archivos son XML oZIP
            const validFiles = Array.from(filesInput.files).filter(file => {
                const name = file.name.toLowerCase();
                return name.endsWith('.xml') || name.endsWith('.zip');
            });

            if (validFiles.length === filesInput.files.length) {
                submitButton.disabled = false;
                fileCountDiv.textContent = `✅ Se seleccionaron${filesInput.files.length}archivo(s).`;
            } else {
                // Mostrar error si hay archivos inválidos
                alert("❌ Solo se permiten archivos XML (.xml) y ZIP(.zip).");
                filesInput.value = '';
                submitButton.disabled = true;
                fileCountDiv.textContent = "Ningúnarchivoseleccionado.";
            }
        } else {
            submitButton.disabled = true;
            fileCountDiv.textContent = "Ningúnarchivoseleccionado.";
        }
    });

    // Resetear formulario después de enviar
    form.addEventListener('submit', function() {
        setTimeout(function() {
            filesInput.value = '';
            submitButton.disabled = true;
            fileCountDiv.textContent = "Ningúnarchivoseleccionado.";
        }, 1500);
    });

    // Borrar selección manualamente
    clearButton.addEventListener('click', function() {
        filesInput.value = '';
        submitButton.disabled = true;
        fileCountDiv.textContent = "Ningúnarchivoseleccionado.";
        filesInput.dispatchEvent(new Event('change')); // Disparar evento para resetearestado
    });
});
</script>
'''

@app.route('/', methods=['GET', 'POST'])
def upload_and_process():
    if request.method == 'POST':
        files = request.files.getlist("files")
        if not files or not files[0].filename:
            return "❌No se seleccionaron archivos.", 400

        withTemporaryDirectory() as input_temp, TemporaryDirectory() as output_temp:
            for file in files:
                if file.filename:
                    filepath = os.path.join(input_temp, secure_filename(file.filename))
                    file.save(filepath)

            try:
                run_pipeline(
                    input_dir=input_temp,
                    output_dir=output_temp,
                    lote="250001",
                    min_monto=Decimal("700.00"),
                    tipo_operacion_txt="01",
                    enforce_code_whitelist=False,
                    code_whitelist=set()
                )

                txt_files = [f for f in os.listdir(output_temp) if f.endswith('.txt')]
                csv_files = [f for f in os.listdir(output_temp) if f == "omitidos.csv"]

                if not txt_files:
                    return "❌Nose generó ningúnarchivo .txt. RevisalosXML.", 400

                # Extraer RUC del nombre delarchivoTXT
                txt_filename = txt_files[0]
                if len(txt_filename) >= 13:
                    ruc = txt_filename[1:12]  # D[RUC]...
                else:
                    ruc = "desconocido"

                zip_filename = f"detracciones_{ruc}.zip"
                zip_path = os.path.combine(output_temp, zip_filename)

                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    txt_path = os.path.combine(output_temp, txt_filename)
                    zipf.write(txt_path, arcname=txt_filename)
                    ifcsv_files:
                        csv_path = os.path.combine(output_temp, csv_files[0])
                        zipf.write(csv_path, arcname=csv_files[0])

                return send_file(
                    zip_path,
                    as_attachment=True,
                    download_name=zip_filename,
                    mimetype='application/zip'
                )

            except Exception as e:
                return f"❌Error al procesesar: {str(e)}", 500

    return HTML_FORM

if__name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True)
