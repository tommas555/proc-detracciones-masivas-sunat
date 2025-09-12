#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import glob
import os
import re
import shutil
import unicodedata
import zipfile
from decimal import Decimal, ROUND_HALF_UP
import xml.etree.ElementTree as ET
from tempfile import TemporaryDirectory

# ---------------------------------------
# Configuración por defecto
# ---------------------------------------
DEFAULT_MIN_MONTO = Decimal("800.00")  # filtro PayableAmount >= 800
DEFAULT_TIPO_OPERACION_TXT = "01"      # usual en servicios/ventas para TXT
# Lista blanca (opcional) de códigos de detracción SUNAT.
DEFAULT_CODE_WHITELIST = {
    # Servicios / construcción / transporte / arrendamiento / inmuebles, etc.
    "022", "030", "037", "039", "040", "041", "042", "043", "044", "045", "046",
    "047", "048", "049", "050", "051", "052", "053", "054", "055"
}
# Namespaces UBL
NS = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "sac": "urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1",
}

# ---------------------------------------
# Utilidades
# ---------------------------------------
def sin_tildes_upper(s: str) -> str:
    s = "" if s is None else str(s)
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return s.upper()

def digits(s: str) -> str:
    s = "" if s is None else str(s)
    return "".join(ch for ch in s if ch.isdigit())

def money15(dec: Decimal) -> str:
    v = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cents = int(v * 100)
    return str(cents).rjust(15, "0")

def periodo_aaaamm(issue_date: str) -> str:
    s = (issue_date or "").strip().replace("/", "-")
    if "-" in s and len(s) >= 7:
        return s[:4] + s[5:7]
    if len(s) == 6 and s.isdigit():
        return s
    return "000000"

def leer_text(elem):
    return elem.text if elem is not None else ""

def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

# ---------------------------------------
# Descubrimiento de archivos
# ---------------------------------------
def list_files_case_insensitive(base_dir: str, patterns):
    paths = []
    for pat in patterns:
        paths.extend(glob.glob(os.path.join(base_dir, pat), recursive=True))
    # Quitar duplicados preservando orden
    seen = set()
    result = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result

def collect_input_files(input_dir: str):
    """Devuelve (zips[], xmls[]) encontrados recursivamente, case-insensitive."""
    zips = list_files_case_insensitive(input_dir, ["**/*.zip", "**/*.ZIP"])
    xmls = list_files_case_insensitive(input_dir, ["**/*.xml", "**/*.XML"])
    return zips, xmls

def extract_xmls_from_zip(zip_path: str, out_dir: str):
    """Extrae solo .xml de un zip (sin rutas internas), devuelve lista de rutas extraídas."""
    extracted = []
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            if member.filename.lower().endswith(".xml"):
                data = z.read(member)
                name = os.path.basename(member.filename)
                out_path = os.path.join(out_dir, name)
                with open(out_path, "wb") as f:
                    f.write(data)
                extracted.append(out_path)
    return extracted

# ---------------------------------------
# Parsing UBL 2.1 (Factura) y validaciones
# ---------------------------------------
def parse_xml_fields(path):
    """
    Devuelve dict con:
    - proveedor_ruc, proveedor_razon, cuenta_bn
    - cliente_doc_tipo, cliente_doc_num, cliente_razon
    - invoice_tipo (01/03), serie (4), numero (8), periodo (aaaamm), issue_date, payable_amount
    - detrac_codigo, detrac_importe (Decimal), tiene_detraccion (bool)
    - source (archivo)
    """
    # Muchos CPE vienen ISO-8859-1/UTF-8, ElementTree auto-detecta desde el prolog
    tree = ET.parse(path)
    root = tree.getroot()

    # Proveedor (emisor)
    prov_id = root.find(".//cac:AccountingSupplierParty/cac:Party/cac:PartyIdentification/cbc:ID", NS)
    proveedor_ruc = digits(leer_text(prov_id))
    prov_razon = root.find(".//cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName", NS)
    proveedor_razon = sin_tildes_upper(leer_text(prov_razon))

    # Cliente (adquiriente)
    cli_id = root.find(".//cac:AccountingCustomerParty/cac:Party/cac:PartyIdentification/cbc:ID", NS)
    cliente_doc_num = digits(leer_text(cli_id))
    cliente_doc_tipo = "6" if len(cliente_doc_num) == 11 else "1"
    cli_razon = root.find(".//cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName", NS)
    cliente_razon = sin_tildes_upper(leer_text(cli_razon))

    # Cabecera del comprobante
    invoice_id = root.find("./cbc:ID", NS)
    comp_id = leer_text(invoice_id).strip()
    serie, numero = ("    ", "00000000")
    if "-" in comp_id:
        s, num = comp_id.split("-", 1)
        serie = sin_tildes_upper(s)[:4].ljust(4, " ")
        numero = digits(num)[-8:].rjust(8, "0")
    else:
        serie = sin_tildes_upper(comp_id)[:4].ljust(4, " ")
        numero = digits(comp_id)[-8:].rjust(8, "0")

    itc = root.find("./cbc:InvoiceTypeCode", NS)
    invoice_tipo = leer_text(itc).strip().zfill(2) if itc is not None else "01"

    issue_date = leer_text(root.find("./cbc:IssueDate", NS))
    periodo = periodo_aaaamm(issue_date)

    pay = root.find(".//cac:LegalMonetaryTotal/cbc:PayableAmount", NS)
    payable_amount = Decimal(leer_text(pay) or "0")

    # Detracción (PaymentTerms: código + monto)
    detr_pt = None
    for pt in root.findall(".//cac:PaymentTerms", NS):
        idv = leer_text(pt.find("./cbc:ID", NS)).strip()
        if idv.lower() == "detraccion":
            detr_pt = pt
            break

    tiene_detraccion = detr_pt is not None
    detrac_codigo = ""
    detrac_importe = Decimal("0")
    if tiene_detraccion:
        detrac_codigo = leer_text(detr_pt.find("./cbc:PaymentMeansID", NS)).strip()
        detr_amount = leer_text(detr_pt.find("./cbc:Amount", NS)).strip() or "0"
        # Sanear coma/punto decimal si viniera con coma
        detr_amount = detr_amount.replace(",", ".")
        detrac_importe = Decimal(detr_amount)

    # Cuenta BN proveedor (PaymentMeans)
    cuenta_bn = ""
    for pm in root.findall(".//cac:PaymentMeans", NS):
        idv = leer_text(pm.find("./cbc:ID", NS)).strip()
        if idv.lower() == "detraccion":
            cuenta_bn = digits(leer_text(pm.find("./cac:PayeeFinancialAccount/cbc:ID", NS)))
            break

    return {
        "proveedor_ruc": proveedor_ruc,
        "proveedor_razon": proveedor_razon,
        "cliente_doc_tipo": cliente_doc_tipo,
        "cliente_doc_num": cliente_doc_num,
        "cliente_razon": cliente_razon,
        "invoice_tipo": invoice_tipo,
        "serie": serie,
        "numero": numero,
        "periodo": periodo,
        "issue_date": issue_date,
        "payable_amount": payable_amount,
        "detrac_codigo": detrac_codigo,
        "detrac_importe": detrac_importe,
        "cuenta_bn": cuenta_bn,
        "tiene_detraccion": tiene_detraccion,
        "source": os.path.basename(path),
        "fullpath": path,
    }

def construir_detalle_proveedor(rec, tipo_operacion_txt="01"):
    """
    Línea de 107 bytes para PROVEEDOR con múltiples adquirientes.
    """
    # 1 tipo doc adquiriente (1)
    tipo_doc = "6" if len(rec["cliente_doc_num"]) == 11 else "1"
    # 2 num doc adquiriente (11)
    num_doc = rec["cliente_doc_num"][-11:].rjust(11, "0")
    # 3 nombre adquiriente (35): si doc=RUC y cod != '040' => 35 espacios
    if tipo_doc == "6" and rec["detrac_codigo"] != "040":
        nombre35 = " " * 35
    else:
        nombre35 = sin_tildes_upper(rec["cliente_razon"])[:35].ljust(35, " ")
    # 4 proforma (9) -> ceros
    proforma = "0".rjust(9, "0")
    # 5 código bien/servicio (3)
    cod_bien = (rec["detrac_codigo"] or "").strip()[:3].rjust(3, "0")
    # 6 cuenta BN proveedor (11)
    cta_bn = rec["cuenta_bn"][-11:].rjust(11, "0")
    # 7 importe detracción (15)
    importe15 = money15(rec["detrac_importe"])
    # 8 tipo operación (2)
    tipo_op = str(tipo_operacion_txt)[:2].rjust(2, "0")
    # 9 periodo (6)
    periodo6 = rec["periodo"][:6].rjust(6, "0")
    # 10 tipo comp (2)
    tipo_comp = rec["invoice_tipo"][:2].rjust(2, "0")
    # 11 serie (4)
    serie4 = rec["serie"][:4].ljust(4, " ")
    # 12 número (8)
    numero8 = rec["numero"][-8:].rjust(8, "0")

    detalle = (
        tipo_doc + num_doc + nombre35 + proforma + cod_bien +
        cta_bn + importe15 + tipo_op + periodo6 + tipo_comp + serie4 + numero8
    )
    if len(detalle) != 107:
        raise ValueError(f"Detalle no mide 107 (mide {len(detalle)}). Fuente: {rec['source']}")
    return detalle

# ---------------------------------------
# Pipeline principal
# ---------------------------------------
def run_pipeline(
    input_dir: str,
    output_dir: str,
    lote: str,
    min_monto: Decimal,
    tipo_operacion_txt: str,
    enforce_code_whitelist: bool,
    code_whitelist: set[str]
):
    ensure_dir(output_dir)
    # Staging temporal para XML extraídos
    with TemporaryDirectory() as tmpdir:
        # 1) recolectar ZIP y XML
        zips, xmls = collect_input_files(input_dir)

        # 2) extraer xml de zip
        extracted = []
        for z in zips:
            try:
                extracted.extend(extract_xmls_from_zip(z, tmpdir))
            except Exception as e:
                print(f"[WARN] ZIP inválido: {z} -> {e}")

        # 3) consolidar todos los xml a procesar (xmls en disco + extraídos)
        all_xmls = []
        # XMLs en input (recursivo, case-insensitive)
        all_xmls.extend(xmls)
        # XMLs extraídos en tmp
        all_xmls.extend(list_files_case_insensitive(tmpdir, ["**/*.xml", "**/*.XML"]))
        # Normalizar rutas únicas
        seen = set()
        final_xmls = []
        for p in all_xmls:
            rp = os.path.realpath(p)
            if rp not in seen:
                seen.add(rp)
                final_xmls.append(rp)

        if not final_xmls:
            raise SystemExit(f"No se encontraron XML en {input_dir} (ni dentro de ZIP).")

        aceptados = []
        omitidos = []

        proveedor_ruc = ""
        proveedor_razon = ""

        for path in sorted(final_xmls):
            try:
                rec = parse_xml_fields(path)
            except Exception as e:
                omitidos.append({"archivo": os.path.basename(path), "motivo": f"XML inválido: {e}"})
                continue

            # Capturar datos proveedor para cabecera desde el primer válido
            if not proveedor_ruc and rec["proveedor_ruc"]:
                proveedor_ruc = rec["proveedor_ruc"]
                proveedor_razon = rec["proveedor_razon"]

            # Validaciones
            if rec["payable_amount"] < min_monto:
                omitidos.append({"archivo": rec["source"], "motivo": f"PayableAmount < {min_monto}"})
                continue

            if not rec["tiene_detraccion"]:
                omitidos.append({"archivo": rec["source"], "motivo": "Sin PaymentTerms[ID='Detraccion']"})
                continue

            if not rec["detrac_codigo"]:
                omitidos.append({"archivo": rec["source"], "motivo": "Sin PaymentMeansID (código detracción)"})
                continue

            # (opcional) lista blanca de códigos
            if enforce_code_whitelist and rec["detrac_codigo"] not in code_whitelist:
                omitidos.append({"archivo": rec["source"], "motivo": f"Código detracción no permitido: {rec['detrac_codigo']}"})
                continue

            if rec["detrac_importe"] <= 0:
                omitidos.append({"archivo": rec["source"], "motivo": "Amount de detracción <= 0"})
                continue

            if not rec["cuenta_bn"]:
                omitidos.append({"archivo": rec["source"], "motivo": "Sin cuenta BN (PaymentMeans/PayeeFinancialAccount/ID)"})
                continue

            try:
                detalle = construir_detalle_proveedor(rec, tipo_operacion_txt=tipo_operacion_txt)
                aceptados.append({"rec": rec, "detalle": detalle})
            except Exception as e:
                omitidos.append({"archivo": rec["source"], "motivo": f"Detalle inválido: {e}"})

        if not aceptados:
            # Escribir omitidos si los hay
            if omitidos:
                out_omit = os.path.join(output_dir, "omitidos.csv")
                with open(out_omit, "w", newline="", encoding="utf-8") as f:
                    wr = csv.writer(f, delimiter=";")
                    wr.writerow(["archivo", "motivo"])
                    for r in omitidos:
                        wr.writerow([r["archivo"], r["motivo"]])
                print(f"[INFO] No hay detalles válidos. Ver {out_omit}")
            else:
                print("[INFO] No hay detalles válidos.")
            return

        # Total del lote (suma de detracciones aceptadas)
        total_cent = 0
        for a in aceptados:
            total_cent += int((a["rec"]["detrac_importe"].quantize(Decimal("0.01")) * 100))
        total15 = str(total_cent).rjust(15, "0")

        # Cabecera (indicador P)
        indicador = "P"
        ruc11 = proveedor_ruc[-11:].rjust(11, "0")
        razon35 = sin_tildes_upper(proveedor_razon)[:35].ljust(35, " ")
        lote6 = str(lote)[-6:].rjust(6, "0")

        cabecera = indicador + ruc11 + razon35 + lote6 + total15
        if len(cabecera) != 68:
            raise SystemExit(f"Cabecera no mide 68 (mide {len(cabecera)}): [{cabecera!r}]")

        # Nombre de salida
        out_name = f"D{ruc11}{lote6}.txt"
        out_path = os.path.join(output_dir, out_name)

        # Escribir TXT
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(cabecera + "\n")
            for a in aceptados:
                f.write(a["detalle"] + "\n")

        print(f"[OK] TXT generado: {out_path}")
        print(f"     Cabecera 68 OK + {len(aceptados)} detalles (107).")

        # Escribir omitidos.csv si hubo
        if omitidos:
            out_omit = os.path.join(output_dir, "omitidos.csv")
            with open(out_omit, "w", newline="", encoding="utf-8") as f:
                wr = csv.writer(f, delimiter=";")
                wr.writerow(["archivo", "motivo"])
                for r in omitidos:
                    wr.writerow([r["archivo"], r["motivo"]])
            print(f"[INFO] Omitidos: {len(omitidos)} (ver {out_omit})")


# def main():
#     parser = argparse.ArgumentParser(
#         description="Genera TXT de Pago Masivo (PROVEEDOR) a partir de XML/ZIP SUNAT."
#     )
#     # Defaults locales (ajusta a tus rutas)
#     DEFAULT_INPUT  = "/home/tom/Documentos/PagoMasivo/XML/"
#     DEFAULT_OUTPUT = "/home/tom/Documentos/PagoMasivo/"
#     DEFAULT_LOTE   = "250001"

#     parser.add_argument("--input",  default=DEFAULT_INPUT,  help="Carpeta de entrada con .xml y/o .zip")
#     parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Carpeta de salida para TXT y reportes")
#     parser.add_argument("--lote",   default=DEFAULT_LOTE,   help="Número de lote AANNNN (ej. 250001)")
#     parser.add_argument("--min-monto", default=str(DEFAULT_MIN_MONTO), help="Mínimo PayableAmount (PEN). Default: 800.00")
#     parser.add_argument("--tipo-op", default=DEFAULT_TIPO_OPERACION_TXT, help="Tipo de operación TXT (2 dígitos). Default: 01")
#     parser.add_argument("--enforce-codes", action="store_true", help="Exigir lista blanca de códigos de detracción")
#     parser.add_argument("--codes", default=",".join(sorted(DEFAULT_CODE_WHITELIST)),
#                         help="Lista blanca de códigos (coma-separados). Sólo si --enforce-codes")

    ##args = parser.parse_args()
    

# if __name__ == "__main__":
#      # En notebook, define directamente:
#     input_dir = "/home/tom/Documentos/PagoMasivo/XML/"
#     output_dir = "/home/tom/Documentos/PagoMasivo/"
#     lote = "250001"
#     min_monto = Decimal("800.00")
#     tipo_op = "01"
#     enforce = False
#     whitelist = set()

#     run_pipeline(
#         input_dir=input_dir,
#         output_dir=output_dir,
#         lote=lote,
#         min_monto=min_monto,
#         tipo_operacion_txt=tipo_op,
#         enforce_code_whitelist=enforce,
#         code_whitelist=whitelist
#     )

