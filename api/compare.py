from flask import Flask, request, jsonify
import pandas as pd
import numpy as np
import io, re, base64

app = Flask(__name__)

def coerce_number(x):
    if pd.isna(x):
        return np.nan
    if isinstance(x, (int, float, np.number)):
        return float(x)
    s = str(x).strip()
    if s == "":
        return np.nan
    s = re.sub(r"[^\\d,.\\-]", "", s)
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return np.nan

def extract_vendedor_from_row(row):
    for cell in row:
        if isinstance(cell, str):
            txt = cell.replace("\\xa0", " ")
            if "vendedor" in txt.lower():
                m = re.search(r"(?i)^\\s*vendedor\\s*:\\s*(.+)\\s*$", txt)
                if m:
                    return m.group(1).strip()
    return None

def extract_sale_number_mov(v):
    if pd.isna(v):
        return None
    s = str(v).replace("\\xa0", " ").strip()
    part = s.split("|")[0]
    m = re.search(r"(\\d+)", part)
    if not m:
        return None
    num = m.group(1).lstrip("0") or "0"
    return num

def extract_sale_number_minhas(v):
    if pd.isna(v):
        return None
    s = str(v).replace("\\xa0", " ").strip()
    m = re.search(r"(\\d+)", s)
    if not m:
        return None
    num = m.group(1).lstrip("0") or "0"
    return num

def read_table(file_storage):
    name = file_storage.filename.lower()
    data = file_storage.read()
    bio = io.BytesIO(data)
    if name.endswith(".xlsx"):
        return pd.read_excel(bio, header=None, dtype=object)
    else:
        return pd.read_csv(bio, header=None, dtype=object, sep=None, engine="python")

def build_movimento_df(raw_df: pd.DataFrame) -> pd.DataFrame:
    current_vendor = None
    vendedores = []
    header_mask = []
    for _, row in raw_df.iterrows():
        vend_here = extract_vendedor_from_row(row.values.tolist())
        if vend_here:
            current_vendor = vend_here
            header_mask.append(True)
        else:
            header_mask.append(False)
        vendedores.append(current_vendor)
    df_full = raw_df.copy()
    df_full["Vendedor"] = vendedores
    df = df_full.loc[[not h for h in header_mask]].copy()
    num = df.iloc[:,2].map(extract_sale_number_mov)
    val = df.iloc[:,7].map(coerce_number)
    out = pd.DataFrame({"numero_venda": num, "valor_movimento": val, "Vendedor": df["Vendedor"]})
    out = out.dropna(subset=["numero_venda"]).reset_index(drop=True)
    return out

def build_minhas_df(raw_df: pd.DataFrame) -> pd.DataFrame:
    num = raw_df.iloc[:,0].map(extract_sale_number_minhas)
    val = raw_df.iloc[:,6].map(coerce_number)
    out = pd.DataFrame({"numero_venda": num, "valor_minhas_vendas": val})
    out = out.dropna(subset=["numero_venda"]).reset_index(drop=True)
    return out

def compare(mov_df, min_df, tol=0.02):
    merged = mov_df.merge(min_df, on="numero_venda", how="outer", indicator=True)
    def cmp(a,b):
        if pd.isna(a) or pd.isna(b):
            return False
        return abs(a-b) <= tol
    merged["bateu"] = merged.apply(lambda r: cmp(r.get("valor_movimento", np.nan), r.get("valor_minhas_vendas", np.nan)), axis=1)
    def cls(r):
        if r["_merge"] == "both":
            return "BATEU" if r["bateu"] else "NAO_BATEU_VALOR"
        elif r["_merge"] == "left_only":
            return "SO_MOVIMENTO"
        else:
            return "SO_MINHAS_VENDAS"
    merged["status"] = merged.apply(cls, axis=1)
    merged["Vendedor"] = merged["Vendedor"].fillna("SEM VENDEDOR (apenas Minhas Vendas)")
    return merged

def to_excel_bytes(merged: pd.DataFrame) -> bytes:
    import re
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary = merged.groupby(["Vendedor","status"], dropna=False).size().unstack(fill_value=0)
        summary["TOTAL"] = summary.sum(axis=1)
        summary = summary.reset_index()
        summary.to_excel(writer, index=False, sheet_name="Resumo")

        merged.loc[merged["status"]=="BATEU"].sort_values(["Vendedor","numero_venda"]).to_excel(
            writer, index=False, sheet_name="Bateram"
        )
        merged.loc[merged["status"]=="NAO_BATEU_VALOR"].sort_values(["Vendedor","numero_venda"]).to_excel(
            writer, index=False, sheet_name="Nao_Bateram_VALOR"
        )
        nao_rel = merged.loc[merged["status"].isin(["SO_MOVIMENTO","SO_MINHAS_VENDAS"])].copy()
        nao_rel["Origem"] = np.where(nao_rel["status"]=="SO_MOVIMENTO", "Somente Movimento", "Somente Minhas Vendas")
        nao_rel.sort_values(["Vendedor","Origem","numero_venda"])[["Vendedor","Origem","numero_venda","valor_movimento","valor_minhas_vendas"]].to_excel(
            writer, index=False, sheet_name="Nao_Encontradas"
        )
        for vend, sub in merged.groupby("Vendedor"):
            safe_name = re.sub(r"[^A-Za-z0-9_\\- ]", "_", str(vend))[:25]
            sub.sort_values(["status","numero_venda"]).to_excel(writer, index=False, sheet_name=f"V_{safe_name}")
    output.seek(0)
    return output.getvalue()

@app.post("/api/compare")
def compare_api():
    if "movimento" not in request.files or "minhas" not in request.files:
        return ("Envie os dois arquivos: 'movimento' e 'minhas'.", 400)
    tol = float(request.form.get("tol", "0.02"))
    mov_raw = read_table(request.files["movimento"])
    minhas_raw = read_table(request.files["minhas"])

    mov_df = build_movimento_df(mov_raw)
    min_df = build_minhas_df(minhas_raw)
    merged = compare(mov_df, min_df, tol=tol)

    total = int(len(merged))
    q_bateu = int((merged["status"]=="BATEU").sum())
    q_nao_bateu_valor = int((merged["status"]=="NAO_BATEU_VALOR").sum())
    q_nao_rel = int(merged["status"].isin(["SO_MOVIMENTO","SO_MINHAS_VENDAS"]).sum())

    resumo = merged.groupby(["Vendedor","status"], dropna=False).size().unstack(fill_value=0)
    for col in ["BATEU","NAO_BATEU_VALOR","SO_MOVIMENTO","SO_MINHAS_VENDAS"]:
        if col not in resumo.columns:
            resumo[col] = 0
    resumo["TOTAL"] = resumo.sum(axis=1)
    resumo = resumo.reset_index()

    def round2(x):
        if x is None or (isinstance(x, float) and np.isnan(x)): return None
        try: return round(float(x), 2)
        except: return None

    bateram = merged.loc[merged["status"]=="BATEU", ["Vendedor","numero_venda","valor_movimento","valor_minhas_vendas"]].sort_values(["Vendedor","numero_venda"])
    nao_val = merged.loc[merged["status"]=="NAO_BATEU_VALOR", ["Vendedor","numero_venda","valor_movimento","valor_minhas_vendas"]].sort_values(["Vendedor","numero_venda"])
    nao_rel = merged.loc[merged["status"].isin(["SO_MOVIMENTO","SO_MINHAS_VENDAS"])].copy()
    nao_rel["Origem"] = np.where(nao_rel["status"]=="SO_MOVIMENTO", "Somente Movimento", "Somente Minhas Vendas")
    cols = ["Vendedor","Origem","numero_venda","valor_movimento","valor_minhas_vendas"]
    nao_rel = nao_rel.sort_values(["Vendedor","Origem","numero_venda"])[cols]

    excel_b = to_excel_bytes(merged)
    excel_b64 = base64.b64encode(excel_b).decode("utf-8")

    payload = {
        "total": total,
        "q_bateu": q_bateu,
        "q_nao_bateu_valor": q_nao_bateu_valor,
        "q_nao_rel": q_nao_rel,
        "resumo": resumo.to_dict(orient="records"),
        "bateram": bateram.assign(
            valor_movimento=bateram["valor_movimento"].map(round2),
            valor_minhas_vendas=bateram["valor_minhas_vendas"].map(round2),
        ).to_dict(orient="records"),
        "nao_bateram_valor": nao_val.assign(
            valor_movimento=nao_val["valor_movimento"].map(round2),
            valor_minhas_vendas=nao_val["valor_minhas_vendas"].map(round2),
        ).to_dict(orient="records"),
        "nao_encontradas": nao_rel.assign(
            valor_movimento=nao_rel["valor_movimento"].map(round2),
            valor_minhas_vendas=nao_rel["valor_minhas_vendas"].map(round2),
        ).to_dict(orient="records"),
        "excel_b64": excel_b64,
    }
    return jsonify(payload)
