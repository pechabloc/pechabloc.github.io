"""Relève quotidienne des posts Instagram du club et tri par rubrique.

Tourne chaque jour sur GitHub (.github/workflows/instagram.yml), dans le dépôt du site :
  1. lit le fil Behold (les 6 derniers posts) ;
  2. ajoute les nouveaux posts à insta/posts.json (l'historique s'accumule) et copie leur image dans insta/images/ ;
  3. range chaque post : hashtag de rubrique, sinon mots-clés de la légende (insta/mots_cles.txt), sinon « à classer » ;
  4. écrit la liste des posts à classer dans le fichier donné par la variable ALERTE (le workflow en fait un ticket GitHub = un mail).

Un post encore présent dans le fil est re-trié à chaque passage : ajouter le hashtag après coup suffit à le ranger.
Classement à la main (prioritaire sur tout) : insta/classement_manuel.txt.
Aucune bibliothèque à installer : Python seul.
"""
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ICI = Path(__file__).parent
FLUX = os.environ.get("FLUX", "https://feeds.behold.so/ypSBSN4rYhYPCdsNcYSE")
POSTS = ICI / "posts.json"
IMAGES = ICI / "images"
RUBRIQUES = {"Ça bouge !": "ca_bouge", "Astuces": "astuces", "Bons réflexes": "reflexes"}
HASHTAGS = {"pechabloc_cabouge": "ca_bouge", "pechabloc_astuces": "astuces", "pechabloc_reflexes": "reflexes"}
A_CLASSER = "a_classer"


def normaliser(texte):
    """minuscules, sans accents, apostrophes et tirets unifiés : « S’Échauffer » -> « s'echauffer »."""
    t = unicodedata.normalize("NFD", texte.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = t.replace("’", "'").replace("‘", "'")
    return re.sub(r"[-_]", " ", t)


def lire_mots_cles(chemin=ICI / "mots_cles.txt"):
    """{rubrique: [motif regex, ...]} ; un mot entier, pluriel en s ou x accepté."""
    regles, rub = {}, None
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        m = re.fullmatch(r"\[(.+)\]", ligne)
        if m:
            rub = RUBRIQUES[m.group(1).strip()]
            regles[rub] = []
        elif ligne and rub:
            mot = re.escape(normaliser(ligne)).replace(r"\ ", r"\s+")
            regles[rub].append((ligne, re.compile(rf"(?<![a-z0-9]){mot}(?:s|x)?(?![a-z0-9])")))
    return regles


def lire_manuel(chemin=ICI / "classement_manuel.txt"):
    """Lignes « lien-ou-identifiant = Rubrique » (Rubrique : Ça bouge ! / Astuces / Bons réflexes / masquer)."""
    manuel = {}
    if chemin.exists():
        for ligne in chemin.read_text(encoding="utf-8").splitlines():
            if "=" in ligne and not ligne.lstrip().startswith("//"):
                cle, rub = (x.strip() for x in ligne.split("=", 1))
                ident = cle.rstrip("/").split("/")[-1]
                manuel[ident] = "masque" if rub.lower() == "masquer" else RUBRIQUES[rub]
    return manuel


def classer(legende, regles):
    """Renvoie (rubrique, raison)."""
    tags = {HASHTAGS[h.lower()] for h in re.findall(r"#(\w+)", legende) if h.lower() in HASHTAGS}
    if len(tags) == 1:
        return tags.pop(), "hashtag"
    if len(tags) > 1:
        return A_CLASSER, "plusieurs hashtags de rubrique"
    texte = normaliser(legende)
    trouves = {rub: [mot for mot, rx in motifs if rx.search(texte)] for rub, motifs in regles.items()}
    trouves = {rub: mots for rub, mots in trouves.items() if mots}
    if len(trouves) == 1:
        rub, mots = trouves.popitem()
        return rub, "mots-clés : " + ", ".join(mots)
    if len(trouves) > 1:
        noms = {v: k for k, v in RUBRIQUES.items()}
        return A_CLASSER, "mots de plusieurs rubriques : " + " / ".join(f"{noms[r]} ({', '.join(m)})" for r, m in trouves.items())
    return A_CLASSER, "ni hashtag ni mot-clé"


def charger(url):
    if re.match(r"https?://", url):
        req = urllib.request.Request(url, headers={"User-Agent": "pechabloc-site"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    return Path(url).read_bytes()          # fichier local (essais)


def image_du_post(p):
    """URL d'une image moyenne du post (vidéo : sa vignette ; carrousel : la 1re image)."""
    tailles = p.get("sizes") or {}
    for t in ("medium", "large", "small", "full"):
        if tailles.get(t, {}).get("mediaUrl"):
            return tailles[t]["mediaUrl"]
    return p.get("thumbnailUrl") or p.get("mediaUrl")


def main():
    flux = json.loads(charger(FLUX))
    regles, manuel = lire_mots_cles(), lire_manuel()
    posts = {p["id"]: p for p in json.loads(POSTS.read_text(encoding="utf-8"))} if POSTS.exists() else {}
    IMAGES.mkdir(exist_ok=True)
    nouveaux = 0
    for p in flux.get("posts", []):
        ident = str(p["id"])
        legende = p.get("caption") or ""
        rub, raison = classer(legende, regles)
        lien = p.get("permalink", "")
        ident_lien = lien.rstrip("/").split("/")[-1]
        for k in (ident, ident_lien):
            if k in manuel:
                rub, raison = manuel[k], "classé à la main"
        ancien = posts.get(ident, {})
        if not ancien:
            nouveaux += 1
            url = image_du_post(p)
            fichier = IMAGES / f"{ident}.jpg"
            if url and not fichier.exists():
                try:
                    fichier.write_bytes(charger(url))
                except Exception as e:                       # l'image manquera, le post reste listé
                    print("image non copiée :", ident, e, file=sys.stderr)
        posts[ident] = {
            "id": ident,
            "date": p.get("timestamp", ancien.get("date", "")),
            "lien": lien,
            "legende": p.get("prunedCaption") or re.sub(r"\s*#\w+", "", legende).strip(),
            "image": f"insta/images/{ident}.jpg" if (IMAGES / f"{ident}.jpg").exists() else "",
            "type": p.get("mediaType", ""),
            "nb_photos": len(p.get("children") or []) or 1,
            "rubrique": rub,
            "raison": raison,
            "alerte_envoyee": ancien.get("alerte_envoyee", False) if rub == A_CLASSER else False,
        }
    # classement à la main aussi pour les posts sortis du fil
    for ident, post in posts.items():
        cle = post["lien"].rstrip("/").split("/")[-1]
        for k in (ident, cle):
            if k in manuel:
                post["rubrique"], post["raison"] = manuel[k], "classé à la main"
    a_signaler = [p for p in posts.values() if p["rubrique"] == A_CLASSER and not p["alerte_envoyee"]]
    alerte = os.environ.get("ALERTE")
    if a_signaler and alerte:
        lignes = [f"{len(a_signaler)} post(s) Instagram n'ont pas pu être rangés automatiquement : ils ne s'affichent pas sur le site.", ""]
        for p in a_signaler:
            lignes += [f"- {p['lien']}", f"  « {p['legende'][:120]} »", f"  raison : {p['raison']}", ""]
        lignes += ["Pour les ranger, au choix :",
                   "1. ajouter dans la légende du post #pechabloc_cabouge, #pechabloc_astuces ou #pechabloc_reflexes (pris en compte au prochain passage, tant que le post est parmi les 6 derniers) ;",
                   "2. ou ajouter une ligne dans insta/classement_manuel.txt, par exemple :",
                   f"   {a_signaler[0]['lien']} = Ça bouge !"]
        Path(alerte).write_text("\n".join(lignes), encoding="utf-8")
        for p in a_signaler:
            p["alerte_envoyee"] = True
    liste = sorted(posts.values(), key=lambda p: p["date"], reverse=True)
    POSTS.write_text(json.dumps(liste, ensure_ascii=False, indent=1), encoding="utf-8")
    (ICI / "derniere_releve.txt").write_text(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") + "\n", encoding="utf-8")
    compte = {r: sum(p["rubrique"] == r for p in liste) for r in ("ca_bouge", "astuces", "reflexes", A_CLASSER, "masque")}
    print(f"fil : {len(flux.get('posts', []))} post(s) ; nouveaux : {nouveaux} ; total : {len(liste)} ; {compte} ; à signaler : {len(a_signaler)}")


if __name__ == "__main__":
    main()
