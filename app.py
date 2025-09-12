# app.py
import os
import zipfile
from flask import Flask, request, send_file, render_template_string
from werkzeug.utils import secure_filename
from decimal import Decimal
from tempfile import TemporaryDirectory

# Importamos tu función run_pipeline desde procesador.py
from procesador import run_pipeline

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB max upload
app.config['SECRET_KEY'] = 'clave_secreta_para_flask_2025'

HTML_FORM = '''
<!doctype html>
<title>Procesador de XML para Detracciones SUNAT</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
<div class="container mt-5">
    <h1 class="mb-4">Procesador de XML/ZIP → TXT Detracciones</h1>
    <form id="uploadForm" method="post" enctype="multipart/form-data" class="mb-4">
        <div class="mb-3">
            <label for="files" class="form-label">Selecciona tus archivos XML o ZIP:</label>
            <input class="form-control" type="file" name="files" id="files" multiple required accept=".xml,.zip" title="Solo XML y ZIP files are allowed">
        </div>
        <div class="mb-3">
            <label for="lote" class="form-label">Número de Lote (6 dígitos, ej: 250001):</label>
            <input class="form-control" type="text" name="lote" id="lote" required pattern="[0-9]{6}" title="Debe ser un número de 6 dígitos" placeholder="Ej: 250001">
            <div class="form-text text-muted">Formato: AANNNN (Año + Número secuencial).</div>
        </div>
        <div class="d-flex gap-2">
            <button type="submit" class="btn btn-primary" disabled>Procesar y Descargar Resultados (TXT + CSV)</button>
            <button type="button" class="btn btn-outline-secondary" id="clearButton">Borrar selección</button>
        </div>
    </form>
    <div class="alert alert-info">
        <strong>Nota:</strong> Este sistema procesa archivos XML de facturas electrónicas y genera el archivo .txt para pago masivo de detracciones en SUNAT, junto con un reporte detallado de omitidos en .csv.
    </div>
</div>

<script>
document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('uploadForm');
    const filesInput = document.getElementById('files');
    const submitButton = form.querySelector('button[type="submit"]');
    const clearButton = document.getElementById('clearButton');

    // Cambiar el texto del botón "Browse..." por "Adjuntar"
    const fileInput = document.querySelector('input[type="file"]');
    if (fileInput) {
        fileInput.style.display = 'none';  // Ocultar el input original
    }

    // Crear un botón personalizado "Adjuntar"
    const adjuntarBtn = document.createElement('button');
    adjuntarBtn.type = 'button';
    adjuntarBtn.className = 'btn btn-light border';
    adjuntarBtn.textContent = 'Adjuntar';
    adjuntarBtn.style.marginRight = '10px';

    // Añadir evento al botón "Adjuntar"
    adjuntarBtn.addEventListener('click', function() {
        filesInput.click();  // Abrir el diálogo de selección de archivos
    });

    // Insertar el botón personalizado antes del input original
    const label = document.querySelector('label[for="files"]');
    label.appendChild(adjuntarBtn);

    // Habilitar/deshabilitar botón y validar lote
    function updateSubmitButton() {
        const loteInput = document.getElementById('lote');
        if (filesInput.files.length > 0 && loteInput.validity.valid) {
            submitButton.disabled = false;
        } else {
            submitButton.disabled = true;
        }
    }

    filesInput.addEventListener('change', updateSubmitButton);
    document.getElementById('lote').addEventListener('input', updateSubmitButton);

    // Resetear formulario después de enviar
    form.addEventListener('submit', function() {
        setTimeout(function() {
            filesInput.value = '';
            document.getElementById('lote').value = '';
            submitButton.disabled = true;
        }, 1500);
    });

    // Borrar selección manualmente
    clearButton.addEventListener('click', function() {
        filesInput.value = '';
        submitButton.disabled = true;
    });
});
</script>
'''

@app.route('/', methods=['GET', 'POST'])
def upload_and_process():
    if request.method == 'POST':
        files = request.files.getlist("files")
        lote = request.form.get("lote", "").strip()

        if not files or not files[0].filename:
            return "❌ No se seleccionaron archivos.", 400

        if not lote or len(lote) != 6 or not lote.isdigit():
            return "❌ El número de lote debe ser un número de 6 dígitos (ej: 250001).", 400

        with TemporaryDirectory() as input_temp:
            with TemporaryDirectory() as output_temp:
                # Guardar archivos subidos
                for file in files:
                    if file.filename:
                        filepath = os.path.join(input_temp, secure_filename(file.filename))
                        file.save(filepath)

                try:
                    run_pipeline(
                        input_dir=input_temp,
                        output_dir=output_temp,
                        lote=lote,  # ✅ Ahora viene del formulario
                        min_monto=Decimal("700.00"),
                        tipo_operacion_txt="01",
                        enforce_code_whitelist=False,
                        code_whitelist=set()
                    )

                    # Buscar archivos generados
                    txt_files = [f for f in os.listdir(output_temp) if f.endswith('.txt')]
                    csv_files = [f for f in os.listdir(output_temp) if f == "omitidos.csv"]

                    if not txt_files:
                        return "❌ No se generó ningún archivo .txt. Revisa los XML.", 400

                    # Extraer RUC del nombre del archivo TXT
                    txt_filename = txt_files[0]
                    if len(txt_filename) >= 13:
                        ruc = txt_filename[1:12]  # D[RUC]...
                    else:
                        ruc = "desconocido"

                    zip_filename = f"detracciones_{ruc}.zip"
                    zip_path = os.path.join(output_temp, zip_filename)

                    # Crear ZIP
                    with zipfile.ZipFile(zip_path, 'w') as zipf:
                        txt_path = os.path.join(output_temp, txt_filename)
                        zipf.write(txt_path, arcname=txt_filename)
                        if csv_files:
                            csv_path = os.path.join(output_temp, csv_files[0])
                            zipf.write(csv_path, arcname=csv_files[0])

                    # Enviar como descarga
                    return send_file(
                        zip_path,
                        as_attachment=True,
                        download_name=zip_filename,
                        mimetype='application/zip'
                    )

                except Exception as e:
                    return f"❌ Error al procesar: {str(e)}", 500

    return HTML_FORM

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True)
