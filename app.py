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
app.config['SECRET_KEY'] = 'tu_clave_secreta_aqui'  # Cambia esto en producción

HTML_FORM = '''
<!doctype html>
<title>Procesador de XML para Detracciones SUNAT</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
<div class="container mt-5">
    <h1 class="mb-4">Procesador de XML/ZIP → TXT Detracciones</h1>
    <form method="post" enctype="multipart/form-data" class="mb-4">
        <div class="mb-3">
            <label for="files" class="form-label">Selecciona tus archivos XML o ZIP:</label>
            <input class="form-control" type="file" name="files" id="files" multiple required>
        </div>
        <button type="submit" class="btn btn-primary">Procesar y Descargar Resultados (TXT + CSV)</button>
    </form>
    <div class="alert alert-info">
        <strong>Nota:</strong> Este sistema procesa archivos XML de facturas electrónicas y genera el archivo .txt para pago masivo de detracciones en SUNAT, junto con un reporte de omitidos en .csv.
    </div>
</div>
'''

@app.route('/', methods=['GET', 'POST'])
def upload_and_process():
    if request.method == 'POST':
        files = request.files.getlist("files")
        if not files or not files[0].filename:
            return "❌ No se seleccionaron archivos.", 400

        # Crear carpetas temporales
        with TemporaryDirectory() as input_temp, TemporaryDirectory() as output_temp:
            # Guardar archivos subidos
            for file in files:
                if file.filename:
                    filepath = os.path.join(input_temp, secure_filename(file.filename))
                    file.save(filepath)

            try:
                # Ejecutar tu pipeline
                run_pipeline(
                    input_dir=input_temp,
                    output_dir=output_temp,
                    lote="250001",
                    min_monto=Decimal("700.00"),  # Cambiado a 700 según SUNAT
                    tipo_operacion_txt="01",
                    enforce_code_whitelist=False,
                    code_whitelist=set()
                )

                # Buscar el archivo .txt generado
                txt_files = [f for f in os.listdir(output_temp) if f.endswith('.txt')]
                # Buscar el archivo omitidos.csv
                csv_files = [f for f in os.listdir(output_temp) if f == "omitidos.csv"]

                if not txt_files:
                    return "❌ No se generó ningún archivo .txt. Revisa los XML.", 400

                # Crear un ZIP con ambos archivos
                zip_filename = "resultados_detracciones.zip"
                zip_path = os.path.join(output_temp, zip_filename)

                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    # Añadir el TXT
                    txt_path = os.path.join(output_temp, txt_files[0])
                    zipf.write(txt_path, arcname=txt_files[0])
                    # Añadir el CSV si existe
                    if csv_files:
                        csv_path = os.path.join(output_temp, csv_files[0])
                        zipf.write(csv_path, arcname=csv_files[0])

                # Enviar el ZIP como descarga
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
