#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import glob
import os
import unicodedata
import zipfile
from decimal import Decimal, ROUND_HALF_UP
import xml.etree.ElementTree as ET
from tempfile import TemporaryDirectory
from collections import Counter

# ---------------------------------------
# Configuración por defecto
# ---------------------------------------
DEFAULT_MIN_MONTO = Decimal("800.00")  # puedes bajarlo a 0.00 si no quieres filtrar por monto
DEFAULT_TIPO_OPERACION_TXT = "01"
DEFAULT_CODE_WHITELIST = {
    "022", "030", "037", "039", "040", "041", "042", "043", "044", "045", "046",
    "047", "048", "049", "050", "051", "052", "053", "054", "055"
}

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
    seen = set()
    result = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result

def collect_input_files(input_dir: str):
    zips = list_files_case_insensitive(input_dir, ["**/*.zip", "**/*.ZIP"])
    xmls = list_files_case_insensitive(input_dir, ["**/*.xml", "**/*.XML"])
    return zips, xmls

def extract_xmls_from_zip(zip_path: str, out_dir: str):
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
# Parsing UBL 2.1 (Factura)
# ---------------------------------------
def parse_xml_fields(path):
    tree = ET.parse(path)
    root = tree.getroot()

    prov_id = root.find(".//cac:AccountingSupplierParty/cac:Party/cac:PartyIdentification/cbc:ID", NS)
    proveedor_ruc = digits(leer_text(prov_id))
    prov_razon = root.find(".//cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName", NS)
    proveedor_razon = sin_tildes_upper(leer_text(prov_razon))

    cli_id = root.find(".//cac:AccountingCustomerParty/cac:Party/cac:PartyIdentification/cbc:ID", NS)
    cliente_doc_num = digits(leer_text(cli_id))
    cliente_doc_tipo = "6" if len(cliente_doc_num) == 11 else "1"
    cli_razon = root.find(".//cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName", NS)
    cliente_razon = sin_tildes_upper(leer_text(cli_razon))

    invoice_id = root.find("./cbc:ID", NS)
    comp_id = leer_text(invoice_id).strip()
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
        detr_amount = detr_amount.replace(",", ".")
        detrac_importe = Decimal(detr_amount)

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
        "comprobante": comp_id,
    }

# ---------------------------------------
# Constructores de detalle (107 bytes)
# ---------------------------------------
def construir_detalle_proveedor(rec, tipo_operacion_txt="01"):
    tipo_doc = "6" if len(rec["cliente_doc_num"]) == 11 else "1"
    num_doc = rec["cliente_doc_num"][-11:].rjust(11, "0")
    if tipo_doc == "6" and rec["detrac_codigo"] != "040":
        nombre35 = " " * 35
    else:
        nombre35 = sin_tildes_upper(rec["cliente_razon"])[:35].ljust(35, " ")
    proforma = "0".rjust(9, "0")
    cod_bien = (rec["detrac_codigo"] or "").strip()[:3].rjust(3, "0")
    cta_bn = rec["cuenta_bn"][-11:].rjust(11, "0")
    importe15 = money15(rec["detrac_importe"])
    tipo_op = str(tipo_operacion_txt)[:2].rjust(2, "0")
    periodo6 = rec["periodo"][:6].rjust(6, "0")
    tipo_comp = rec["invoice_tipo"][:2].rjust(2, "0")
    serie4 = rec["serie"][:4].ljust(4, " ")
    numero8 = rec["numero"][-8:].rjust(8, "0")

    detalle = (
        tipo_doc + num_doc + nombre35 + proforma + cod_bien +
        cta_bn + importe15 + tipo_op + periodo6 + tipo_comp + serie4 + numero8
    )
    if len(detalle) != 107:
        raise ValueError(f"Detalle proveedor no mide 107 (mide {len(detalle)})")
    return detalle

def construir_detalle_adquiriente(rec, tipo_operacion_txt="01"):
    # Caso Adquiriente/Internet: proveedor siempre RUC, nombre EN BLANCO (35)
    tipo_doc = "6"
    num_doc = rec["proveedor_ruc"][-11:].rjust(11, "0")
    nombre35 = " " * 35
    proforma = "0".rjust(9, "0")
    cod_bien = (rec["detrac_codigo"] or "").strip()[:3].rjust(3, "0")
    cta_bn = rec["cuenta_bn"][-11:].rjust(11, "0")
    importe15 = money15(rec["detrac_importe"])
    tipo_op = str(tipo_operacion_txt)[:2].rjust(2, "0")
    periodo6 = rec["periodo"][:6].rjust(6, "0")
    tipo_comp = rec["invoice_tipo"][:2].rjust(2, "0")
    serie4 = rec["serie"][:4].ljust(4, " ")
    numero8 = rec["numero"][-8:].rjust(8, "0")

    detalle = (
        tipo_doc + num_doc + nombre35 + proforma + cod_bien +
        cta_bn + importe15 + tipo_op + periodo6 + tipo_comp + serie4 + numero8
    )
    if len(detalle) != 107:
        raise ValueError(f"Detalle adquiriente no mide 107 (mide {len(detalle)})")
    return detalle

# ---------------------------------------
# Helpers
# ---------------------------------------
def add_omit(omitidos, rec_or_none, archivo, motivo):
    row = {
        "archivo": archivo,
        "comprobante": "",
        "motivo": motivo,
        "payable_amount": "",
        "detrac_codigo": "",
        "detrac_importe": "",
    }
    if rec_or_none:
        row["comprobante"] = rec_or_none.get("comprobante", "")
        pa = rec_or_none.get("payable_amount", None)
        if isinstance(pa, Decimal):
            row["payable_amount"] = f"{pa:.2f}"
        row["detrac_codigo"] = rec_or_none.get("detrac_codigo", "")
        di = rec_or_none.get("detrac_importe", None)
        if isinstance(di, Decimal):
            row["detrac_importe"] = f"{di:.2f}"
    omitidos.append(row)

def _preflight_validate(records, tipo_depositante):
    """
    Verifica coherencias típicas; NO aborta por proveedor no-RUC en modo 'adquiriente'
    (eso ahora se omite registro por registro en el bucle principal).
    """
    if not records:
        return
    proveedores = {r.get("proveedor_ruc") for r in records if r.get("proveedor_ruc")}
    adquirientes = {r.get("cliente_doc_num") for r in records if r.get("cliente_doc_num")}
    cuentas_bn = {r.get("cuenta_bn") for r in records if r.get("cuenta_bn")}

    if tipo_depositante == "adquiriente":
        # Solo exigimos que sea el MISMO adquiriente en todo el lote
        if len(adquirientes) != 1:
            raise RuntimeError(
                f"Modo 'adquiriente' seleccionado, pero se detectaron {len(adquirientes)} adquirientes distintos en los XML. "
                "Se recomienda separar por adquiriente o elegir el modo correcto."
            )
    else:
        # Modo proveedor: un solo proveedor y una sola cuenta BN
        if len(proveedores) != 1:
            raise RuntimeError(
                f"Modo 'proveedor' seleccionado, pero hay {len(proveedores)} proveedores distintos en los XML. "
                "Este caso exige un único proveedor. Se sugiere separar por proveedor o elegir el modo 'adquiriente'."
            )
        if len(cuentas_bn) != 1:
            raise RuntimeError(
                "Modo 'proveedor': se detectaron múltiples cuentas BN en los detalles. "
                "En este caso la cuenta BN del proveedor debe ser única y repetirse en todos los registros."
            )

def _resumen_omitidos(omitidos, max_motivos=5):
    if not omitidos:
        return "No hay registros en omitidos."
    motivos = [row.get("motivo","(sin motivo)") for row in omitidos]
    top = Counter(motivos).most_common(max_motivos)
    partes = [f"{m} → {c}" for m, c in top]
    return "Motivos principales: " + "; ".join(partes)

# ---------------------------------------
# Pipeline
# ---------------------------------------
def run_pipeline(
    input_dir: str,
    output_dir: str,
    lote: str,
    min_monto: Decimal = DEFAULT_MIN_MONTO,
    tipo_operacion_txt: str = DEFAULT_TIPO_OPERACION_TXT,
    enforce_code_whitelist: bool = False,
    code_whitelist: set[str] = DEFAULT_CODE_WHITELIST,
    tipo_depositante: str = "proveedor"
):
    ensure_dir(output_dir)

    with TemporaryDirectory() as tmpdir:
        zips, xmls = collect_input_files(input_dir)
        extracted = []
        for z in zips:
            try:
                extracted.extend(extract_xmls_from_zip(z, tmpdir))
            except Exception as e:
                print(f"[WARN] ZIP inválido: {z} -> {e}")

        all_xmls = []
        all_xmls.extend(xmls)
        all_xmls.extend(list_files_case_insensitive(tmpdir, ["**/*.xml", "**/*.XML"]))

        seen = set()
        final_xmls = []
        for p in all_xmls:
            rp = os.path.realpath(p)
            if rp not in seen:
                seen.add(rp)
                final_xmls.append(rp)

        if not final_xmls:
            raise RuntimeError("No se encontraron XML válidos.")

        aceptados, omitidos = [], []
        proveedor_ruc, proveedor_razon = "", ""
        cliente_ruc, cliente_razon = "", ""

        for path in sorted(final_xmls):
            try:
                rec = parse_xml_fields(path)
            except Exception as e:
                add_omit(omitidos, None, os.path.basename(path), f"XML inválido: {e}")
                continue

            if not proveedor_ruc and rec["proveedor_ruc"]:
                proveedor_ruc, proveedor_razon = rec["proveedor_ruc"], rec["proveedor_razon"]
            if not cliente_ruc and rec["cliente_doc_num"]:
                cliente_ruc, cliente_razon = rec["cliente_doc_num"], rec["cliente_razon"]

            # Filtros básicos/formales
            if rec["payable_amount"] < min_monto:
                add_omit(omitidos, rec, rec["source"], f"PayableAmount < {min_monto}")
                continue
            if not rec["tiene_detraccion"]:
                add_omit(omitidos, rec, rec["source"], "Sin PaymentTerms[ID='Detraccion']")
                continue
            if not rec["detrac_codigo"]:
                add_omit(omitidos, rec, rec["source"], "Sin PaymentMeansID (código detracción)")
                continue
            if enforce_code_whitelist and rec["detrac_codigo"] not in code_whitelist:
                add_omit(omitidos, rec, rec["source"], f"Código no permitido: {rec['detrac_codigo']}")
                continue
            if rec["detrac_importe"] <= 0:
                add_omit(omitidos, rec, rec["source"], "Importe de detracción <= 0")
                continue
            if not rec["cuenta_bn"]:
                add_omit(omitidos, rec, rec["source"], "Sin cuenta BN")
                continue

            # En modo ADQUIRIENTE, omitir proveedores que no sean RUC (11 dígitos)
            if tipo_depositante == "adquiriente" and len(rec["proveedor_ruc"]) != 11:
                add_omit(omitidos, rec, rec["source"], "Proveedor no RUC (modo adquiriente)")
                continue

            # Construcción de detalle (107 bytes)
            try:
                if tipo_depositante == "proveedor":
                    detalle = construir_detalle_proveedor(rec, tipo_operacion_txt)
                else:
                    detalle = construir_detalle_adquiriente(rec, tipo_operacion_txt)
                aceptados.append({"rec": rec, "detalle": detalle})
            except Exception as e:
                add_omit(omitidos, rec, rec["source"], f"Detalle inválido: {e}")

        # Validación de coherencia (ya no revisa RUC de proveedor en modo adquiriente)
        accepted_recs = [a["rec"] for a in aceptados]
        _preflight_validate(accepted_recs, tipo_depositante)

        # Sin aceptados -> no escribir TXT, sólo omitidos + mensaje claro
        if not aceptados:
            if omitidos:
                out_omit = os.path.join(output_dir, "omitidos.csv")
                with open(out_omit, "w", newline="", encoding="utf-8") as f:
                    wr = csv.writer(f, delimiter=";")
                    wr.writerow([
                        "Archivo XML",
                        "Comprobante (Serie-Número)",
                        "Motivo de omisión",
                        "Total del comprobante (PEN)",
                        "Código de detracción",
                        "Importe de detracción (PEN)"
                    ])
                    for r in omitidos:
                        wr.writerow([
                            r["archivo"],
                            r["comprobante"],
                            r["motivo"],
                            r["payable_amount"],
                            r["detrac_codigo"],
                            r["detrac_importe"]
                        ])
                resumen = _resumen_omitidos(omitidos)
                raise RuntimeError("No se generó el TXT porque no hay registros válidos (todos fueron omitidos). " + resumen)
            else:
                raise RuntimeError("No se generó el TXT: no se encontraron XML válidos.")

        # Total lote (centavos, 15 posiciones)
        total_cent = sum(int((a["rec"]["detrac_importe"].quantize(Decimal("0.01")) * 100)) for a in aceptados)
        total15 = str(total_cent).rjust(15, "0")

        # Cabecera (68 bytes)
        if tipo_depositante == "proveedor":
            indicador = "P"
            ruc11 = proveedor_ruc[-11:].rjust(11, "0")
            razon35 = sin_tildes_upper(proveedor_razon)[:35].ljust(35, " ")
        else:
            indicador = "*"  # Adquiriente/Internet
            ruc11 = cliente_ruc[-11:].rjust(11, "0")
            razon35 = sin_tildes_upper(cliente_razon)[:35].ljust(35, " ")

        lote6 = str(lote)[-6:].rjust(6, "0")
        cabecera = indicador + ruc11 + razon35 + lote6 + total15
        if len(cabecera) != 68:
            raise RuntimeError(f"Cabecera no mide 68 (mide {len(cabecera)})")

        # Escribir TXT
        out_name = f"D{ruc11}{lote6}.txt"
        out_path = os.path.join(output_dir, out_name)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(cabecera + "\n")
            for a in aceptados:
                f.write(a["detalle"] + "\n")

        print(f"[OK] TXT generado: {out_path} ({len(aceptados)} detalles)")

        # Escribir omitidos.csv si corresponde (mismos encabezados claros)
        if omitidos:
            out_omit = os.path.join(output_dir, "omitidos.csv")
            with open(out_omit, "w", newline="", encoding="utf-8") as f:
                wr = csv.writer(f, delimiter=";")
                wr.writerow([
                    "Archivo XML",
                    "Comprobante (Serie-Número)",
                    "Motivo de omisión",
                    "Total del comprobante (PEN)",
                    "Código de detracción",
                    "Importe de detracción (PEN)"
                ])
                for r in omitidos:
                    wr.writerow([
                        r["archivo"],
                        r["comprobante"],
                        r["motivo"],
                        r["payable_amount"],
                        r["detrac_codigo"],
                        r["detrac_importe"]
                    ])
