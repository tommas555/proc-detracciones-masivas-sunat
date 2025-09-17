<!doctype html>
<title>Procesador de XML para Detracciones SUNAT</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
<div class="container mt-5">
    <h1 class="mb-4">Procesador de XML/ZIP → TXT Detracciones</h1>
    <form id="uploadForm" method="post" enctype="multipart/form-data" class="mb-4">
        <div class="mb-3">
            <label for="files" class="form-label">Selecciona tus archivos XML o ZIP:</label>
            <div class="d-flex flex-column gap-2">
                <div class="d-flex align-items-center gap-2">
                    <button type="button" class="btn btn-light border" id="adjuntarBtn">Adjuntar archivos</button>
                    <div id="fileCount" class="text-muted">Ningún archivo seleccionado</div>
                </div>
                <div id="fileList" class="mt-2 small text-muted" style="max-height: 150px; overflow-y: auto;"></div>
            </div>
            <input class="form-control" type="file" name="files" id="files" multiple required accept=".xml,.zip" title="Solo XML y ZIP" style="display: none;">
        </div>
        <div class="mb-3">
            <label for="lote" class="form-label">Número de Lote (6 dígitos, ej: 250001):</label>
            <input class="form-control" type="text" name="lote" id="lote" required pattern="[0-9]{6}" title="Debe ser un número de 6 dígitos" placeholder="Ej: 250001">
            <div class="form-text text-muted">Formato: AANNNN (Año + Número secuencial).</div>
        </div>
        <div class="d-flex gap-2">
            <button type="submit" class="btn btn-primary" id="submitButton" disabled>Procesar y Descargar Resultados (TXT + CSV)</button>
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
    const adjuntarBtn = document.getElementById('adjuntarBtn');
    const fileCountDiv = document.getElementById('fileCount');
    const fileListDiv = document.getElementById('fileList');
    const submitButton = document.getElementById('submitButton');
    const clearButton = document.getElementById('clearButton');
    const loteInput = document.getElementById('lote');

    // Abrir selector de archivos al hacer clic en "Adjuntar"
    adjuntarBtn.addEventListener('click', function() {
        filesInput.click();
    });

    // Actualizar la lista de archivos seleccionados
    function updateFileList() {
        if (filesInput.files.length > 0) {
            let fileListHTML = '<strong>Archivos seleccionados:</strong><ul class="mb-0 mt-1">';
            for (let i = 0; i < filesInput.files.length; i++) {
                fileListHTML += `<li>${filesInput.files[i].name}</li>`;
            }
            fileListHTML += '</ul>';
            fileListDiv.innerHTML = fileListHTML;
        } else {
            fileListDiv.innerHTML = '';
        }
    }

    // Actualizar contador y estado del botón "Procesar"
    function updateUI() {
        const hasFiles = filesInput.files.length > 0;
        const validLote = loteInput.validity.valid;
        
        if (hasFiles) {
            fileCountDiv.textContent = `✅ ${filesInput.files.length} archivo(s) seleccionado(s)`;
            fileCountDiv.className = 'text-success fw-bold';
        } else {
            fileCountDiv.textContent = "Ningún archivo seleccionado";
            fileCountDiv.className = 'text-muted';
        }
        
        submitButton.disabled = !(hasFiles && validLote);
        
        updateFileList();
    }

    // Eventos
    filesInput.addEventListener('change', updateUI);
    loteInput.addEventListener('input', updateUI);

    // Resetear después de enviar
    form.addEventListener('submit', function() {
        // Mostrar mensaje de procesamiento
        submitButton.disabled = true;
        submitButton.textContent = 'Procesando...';
        
        setTimeout(function() {
            submitButton.textContent = 'Procesar y Descargar Resultados (TXT + CSV)';
        }, 2000);
    });

    // Borrar selección manualmente
    clearButton.addEventListener('click', function() {
        filesInput.value = '';
        updateUI();
    });

    // Validación inicial
    updateUI();
});
</script>
