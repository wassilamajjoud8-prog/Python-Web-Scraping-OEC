import csv
import json
import os
import re
import time
from playwright.sync_api import sync_playwright

DOSSIER_SORTIE = r"C:\Users\o.karmaoui\Downloads"


def nettoyer_valeur(valeur):
    """Extrait proprement le texte, même s'il s'agit d'un dictionnaire GraphQL/JSON."""
    if isinstance(valeur, dict):
        return (
            valeur.get("name")
            or valeur.get("label")
            or valeur.get("nom")
            or valeur.get("title")
            or ""
        )
    if isinstance(valeur, list):
        return ", ".join([nettoyer_valeur(v) for v in valeur if v])
    if valeur is None:
        return ""
    return re.sub(r"\s+", " ", str(valeur)).strip()


def extraire_experts_depuis_json(payload):
    """Parcourt récursivement les réponses JSON/GraphQL pour extraire les experts."""
    resultats = []

    def explorer(obj):
        if isinstance(obj, dict):
            # Vérifie si le dictionnaire courant correspond à une fiche d'expert
            champs_cles = [k.lower() for k in obj.keys()]
            if any(
                k in champs_cles
                for k in ["email", "mail", "company", "societe", "raisonsociale"]
            ) and any(
                k in champs_cles
                for k in ["nom", "name", "lastname", "firstname", "displayname"]
            ):
                nom = (
                    obj.get("name")
                    or obj.get("nom")
                    or f"{obj.get('firstName', '')} {obj.get('lastName', '')}".strip()
                    or obj.get("displayName")
                    or ""
                )

                societe = (
                    obj.get("company")
                    or obj.get("societe")
                    or obj.get("cabinet")
                    or obj.get("raisonSociale")
                    or obj.get("raison_sociale")
                    or ""
                )

                ville = obj.get("city") or obj.get("ville") or obj.get("town") or ""

                email = (
                    obj.get("email")
                    or obj.get("mail")
                    or obj.get("e_mail")
                    or ""
                )

                tel = (
                    obj.get("phone")
                    or obj.get("telephone")
                    or obj.get("gsm")
                    or obj.get("mobile")
                    or obj.get("tel")
                    or obj.get("fixe")
                    or obj.get("phoneNumber")
                    or ""
                )

                nom_propre = nettoyer_valeur(nom)
                if nom_propre:
                    resultats.append(
                        {
                            "Nom": nom_propre,
                            "Société": nettoyer_valeur(societe),
                            "Ville": nettoyer_valeur(ville),
                            "Email": nettoyer_valeur(email),
                            "Téléphone": nettoyer_valeur(tel),
                        }
                    )
            else:
                for v in obj.values():
                    explorer(v)
        elif isinstance(obj, list):
            for item in obj:
                explorer(item)

    explorer(payload)
    return resultats


def main():
    url = "https://www.oec.ma/annuaire"
    reponses_api = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        # Interception de toutes les réponses JSON (GraphQL/REST)
        def intercepter_reponse(response):
            if "json" in response.headers.get("content-type", ""):
                try:
                    reponses_api.append(response.json())
                except Exception:
                    pass

        page.on("response", intercepter_reponse)

        print(f"[INFO] Connexion à {url}...")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)

        # Lancement de la recherche initiale
        boutons_recherche = [
            "button:has-text('Rechercher')",
            "button[type='submit']",
            "input[type='submit']",
            ".btn-primary",
        ]

        for sel in boutons_recherche:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                print(f"[INFO] Clic sur le bouton de recherche : '{sel}'")
                loc.first.click()
                page.wait_for_timeout(4000)
                break

        # Parcours de la pagination
        page_num = 1
        max_pages = 80  # Limite de sécurité

        while page_num < max_pages:
            print(f"[INFO] Navigation page {page_num}...")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)

            total_captures_avant = len(reponses_api)

            # Recherche des boutons de page suivante ou des numéros de page
            bouton_suivant = page.locator(
                f"button:has-text('{page_num + 1}'), a:has-text('{page_num + 1}'), "
                f"a:has-text('>'), button:has-text('>'), .pagination-next, "
                f"a:has-text('Suivant'), button:has-text('Suivant')"
            ).first

            if (
                bouton_suivant.count() > 0
                and bouton_suivant.is_visible()
                and bouton_suivant.is_enabled()
            ):
                try:
                    bouton_suivant.click()
                    page_num += 1
                    page.wait_for_timeout(3000)
                except Exception:
                    print("[INFO] Fin de la pagination atteinte.")
                    break
            else:
                print("[INFO] Plus de bouton 'Suivant' disponible.")
                break

            # Si aucune nouvelle requête réseau n'a été déclenchée, on stoppe
            if len(reponses_api) == total_captures_avant:
                print("[INFO] Aucune donnée supplémentaire reçue.")
                break

        browser.close()

    # Traitement des réponses capturées
    print("[INFO] Traitement et nettoyage des fiches extraites...")
    tous_les_experts = []
    for resp in reponses_api:
        tous_les_experts.extend(extraire_experts_depuis_json(resp))

    # Dédoublonnage par Nom + Email/Ville
    fiches_uniques = {}
    for exp in tous_les_experts:
        cle = (exp["Nom"].lower(), exp["Email"].lower() or exp["Ville"].lower())
        if exp["Nom"] and cle not in fiches_uniques:
            fiches_uniques[cle] = exp

    liste_finale = list(fiches_uniques.values())
    print(f"\n[RÉSULTAT FINAL] Total d'experts extraits : {len(liste_finale)}")

    if not liste_finale:
        print("[AVERTISSEMENT] Aucune donnée n'a été capturée.")
        return

    os.makedirs(DOSSIER_SORTIE, exist_ok=True)

    # Export CSV pour Excel Windows (utf-8-sig avec séparateur ;)
    path_csv = os.path.join(DOSSIER_SORTIE, "experts_comptables_maroc.csv")
    with open(path_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Nom", "Société", "Ville", "Email", "Téléphone"],
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(liste_finale)
    print(f"[SUCCÈS] Fichier CSV généré : {path_csv}")

    # Export JSON
    path_json = os.path.join(DOSSIER_SORTIE, "experts_comptables_maroc.json")
    with open(path_json, "w", encoding="utf-8") as f:
        json.dump(liste_finale, f, ensure_ascii=False, indent=2)
    print(f"[SUCCÈS] Fichier JSON généré : {path_json}")

    print("\n--- Aperçu des 5 premières fiches ---")
    for i, m in enumerate(liste_finale[:5], 1):
        print(f"\nFiche {i} :")
        print(f"  Nom       : {m['Nom']}")
        print(f"  Société   : {m['Société']}")
        print(f"  Ville     : {m['Ville']}")
        print(f"  Email     : {m['Email']}")
        print(f"  Téléphone : {m['Téléphone']}")


if __name__ == "__main__":
    main()
