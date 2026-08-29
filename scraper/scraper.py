#!/usr/bin/env python3
"""
Scraper de notícias jurídicas — raspa RSS feeds e salva em src/data/noticias.json
Executa via GitHub Actions a cada 4 horas.
"""

import json
import re
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

import unicodedata

import feedparser
import httpx
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fontes RSS
# ---------------------------------------------------------------------------
FONTES = [
    {
        "nome": "Conjur",
        "url": "https://www.conjur.com.br/rss.xml",
        "categoria_padrao": "Geral",
    },
    {
        "nome": "JOTA",
        "url": "https://www.jota.info/feed",
        "categoria_padrao": "Constitucional",
    },
    {
        "nome": "TST",
        "url": "https://www.tst.jus.br/rss",
        "categoria_padrao": "Trabalhista",
    },
    {
        "nome": "Âmbito Jurídico",
        "url": "https://ambitojuridico.com.br/feed",
        "categoria_padrao": "Geral",
    },
    {
        "nome": "OAB",
        "url": "https://www.oab.org.br/rss",
        "categoria_padrao": "Geral",
    },
    {
        "nome": "Previdenciarista",
        "url": "https://www.previdenciarista.com/feed",
        "categoria_padrao": "Previdenciário",
        "verify_ssl": False,
    },
    {
        "nome": "IBDP",
        "url": "https://ibdp.org.br/feed",
        "categoria_padrao": "Previdenciário",
    },
    {
        "nome": "Agência Brasil",
        "url": "https://agenciabrasil.ebc.com.br/rss/justica/feed.xml",
        "categoria_padrao": "Geral",
    },
    {
        "nome": "Metrópoles",
        "url": "https://www.metropoles.com/brasil/justica/feed",
        "categoria_padrao": "Geral",
    },
]

# ---------------------------------------------------------------------------
# Categorização por palavras-chave
# ---------------------------------------------------------------------------
CATEGORIAS = {
    "Constitucional": [
        "stf", "supremo", "constitucional", "adi", "adpf", "re ", "mandado de segurança",
        "inconstitucional", "constituição", "habeas corpus", "hc ", "liminar",
    ],
    "Trabalhista": [
        "trabalhista", "trabalho", "empregado", "empregador", "clt", "tst", "trt",
        "rescisão", "demissão", "salário", "horas extras", "fgts", "reintegração",
        "assédio moral", "assédio sexual no trabalho",
    ],
    "Tributário": [
        "tribut", "imposto", "icms", "iss", "irpf", "irpj", "csll", "pis", "cofins",
        "receita federal", "fisco", "contribuição", "isenção", "alíquota", "desonera",
    ],
    "Penal": [
        "crime", "pena", "prisão", "detido", "preso", "réu", "acusado", "condenado",
        "absolvido", "júri", "homicídio", "furto", "roubo", "estelionato", "lavagem",
        "corrupção", "peculato", "tráfico",
    ],
    "Civil": [
        "indenização", "dano moral", "responsabilidade civil", "contrato", "divórcio",
        "guarda", "alimentos", "herança", "testamento", "posse", "propriedade",
        "consumidor", "procon", "familia", "casamento",
    ],
    "Previdenciário": [
        "inss", "previdência", "aposentadoria", "benefício", "pensão", "auxílio",
        "segurado", "bpc", "loas", "incapacidade", "perícia médica",
    ],
}


def categorizar(texto: str) -> str:
    texto_l = texto.lower()
    scores: dict[str, int] = {cat: 0 for cat in CATEGORIAS}
    for cat, palavras in CATEGORIAS.items():
        for p in palavras:
            if p in texto_l:
                scores[cat] += 1
    melhor = max(scores, key=lambda c: scores[c])
    return melhor if scores[melhor] > 0 else "Geral"


def limpar_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(separator=" ")).strip()


def sanitizar_html(html: str) -> str:
    """Remove scripts/iframes mas mantém parágrafos, listas e imagens."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "iframe", "form", "input", "button", "nav", "aside"]):
        tag.decompose()
    # Remove atributos perigosos mantendo href/src
    for tag in soup.find_all(True):
        attrs_manter = {}
        for attr in ("href", "src", "alt", "title"):
            if tag.get(attr):
                attrs_manter[attr] = tag[attr]
        tag.attrs = attrs_manter
    return str(soup)


def _ascii(texto: str) -> str:
    """Remove acentos via decomposição Unicode — funciona para qualquer idioma."""
    return unicodedata.normalize('NFD', texto).encode('ascii', 'ignore').decode('ascii')

def slug(titulo: str, fonte: str) -> str:
    h = hashlib.md5(f"{fonte}:{titulo}".encode()).hexdigest()[:8]
    t = _ascii(titulo.lower())
    s = re.sub(r"[^a-z0-9]+", "-", t[:70]).strip("-")
    return f"{s}-{h}"


def parse_data(entry) -> str:
    for campo in ("published", "updated", "created"):
        raw = getattr(entry, campo, None)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt.astimezone(timezone.utc).isoformat()
            except Exception:
                pass
    return datetime.now(timezone.utc).isoformat()


def extrair_imagem(entry) -> str | None:
    for m in getattr(entry, "media_content", []):
        url = m.get("url", "")
        if url and any(url.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
            return url
    for enc in getattr(entry, "enclosures", []):
        if enc.get("type", "").startswith("image/"):
            return enc.get("href")
    # Tenta extrair do conteúdo completo
    for campo in ("content", "summary"):
        raw = ""
        if campo == "content":
            conteudo = getattr(entry, "content", [])
            raw = conteudo[0].get("value", "") if conteudo else ""
        else:
            raw = getattr(entry, "summary", "") or ""
        m = re.search(r'<img[^>]+src=["\']([^"\']{10,})["\']', raw)
        if m:
            url = m.group(1)
            if url.startswith("http"):
                return url
    return None


def extrair_conteudo(entry) -> str:
    """Pega o conteúdo completo do artigo do RSS (content:encoded > summary)."""
    # content:encoded (campo mais rico)
    conteudo_list = getattr(entry, "content", [])
    if conteudo_list:
        raw = conteudo_list[0].get("value", "")
        if raw and len(raw) > 200:
            return sanitizar_html(raw)

    # summary (fallback)
    raw = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
    if raw:
        return sanitizar_html(raw)

    return ""


HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DireitoEmPauta/1.0; +https://direitoempauta.com.br)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


def scrape_fonte(fonte: dict) -> list[dict]:
    log.info(f"Raspando {fonte['nome']}...")
    verify = fonte.get("verify_ssl", True)
    try:
        resp = httpx.get(fonte["url"], headers=HEADERS, timeout=20, follow_redirects=True, verify=verify)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning(f"  Erro ao buscar {fonte['nome']}: {e}")
        return []

    itens = []
    for entry in feed.entries[:30]:
        titulo = limpar_html(getattr(entry, "title", "")).strip()
        if not titulo:
            continue

        link = getattr(entry, "link", "")
        if not link:
            continue

        conteudo = extrair_conteudo(entry)
        resumo = limpar_html(conteudo)[:300] if conteudo else ""

        texto_cat = f"{titulo} {resumo}"
        categoria = categorizar(texto_cat)
        if categoria == "Geral":
            categoria = fonte["categoria_padrao"]

        itens.append({
            "id":        slug(titulo, fonte["nome"]),
            "titulo":    titulo,
            "resumo":    resumo,
            "conteudo":  conteudo,   # HTML completo do artigo
            "link":      link,        # Link original (para atribuição)
            "fonte":     fonte["nome"],
            "categoria": categoria,
            "data":      parse_data(entry),
            "imagem":    extrair_imagem(entry),
        })

    log.info(f"  {len(itens)} itens coletados de {fonte['nome']}")
    return itens


def main():
    saida = Path(__file__).parent.parent / "src" / "data" / "noticias.json"

    existentes: dict[str, dict] = {}
    if saida.exists():
        try:
            for n in json.loads(saida.read_text("utf-8")):
                existentes[n["id"]] = n
        except Exception:
            pass

    novos = 0
    for fonte in FONTES:
        for item in scrape_fonte(fonte):
            if item["id"] not in existentes:
                existentes[item["id"]] = item
                novos += 1
            else:
                # Atualiza conteúdo se ficou mais rico
                old = existentes[item["id"]]
                if len(item.get("conteudo", "")) > len(old.get("conteudo", "")):
                    old["conteudo"] = item["conteudo"]
                old["resumo"] = item["resumo"]

    todas = sorted(existentes.values(), key=lambda x: x["data"], reverse=True)[:500]

    saida.write_text(json.dumps(todas, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"✓ {novos} novas notícias adicionadas. Total: {len(todas)}")


if __name__ == "__main__":
    main()
