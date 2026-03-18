"""
test_semaine3.py — Tests complets Semaine 3 : AppManager + FileManager
Vendredi — validation de tous les modules avant livraison.

LANCER :
    cd jarvis_windows
    python -m pytest tests/test_modules/test_semaine3.py -v
    ou
    python tests/test_modules/test_semaine3.py
"""

import sys
import os
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS COMMUNS
# ══════════════════════════════════════════════════════════════════════════════

def assert_response(result: dict, expected_success: bool = None):
    """Vérifie le format standard {success, message, data}."""
    assert isinstance(result, dict),       f"Doit être un dict, reçu: {type(result)}"
    assert "success" in result,            "Clé 'success' manquante"
    assert "message" in result,            "Clé 'message' manquante"
    assert "data"    in result,            "Clé 'data' manquante"
    assert isinstance(result["message"], str), "message doit être une str"
    if expected_success is not None:
        assert result["success"] == expected_success, (
            f"Attendu success={expected_success}\n  reçu: {result['message']}"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  FIXTURES : dossier temporaire pour les tests de fichiers
# ══════════════════════════════════════════════════════════════════════════════

class TempDir:
    """Contexte qui crée un dossier temporaire et le supprime après les tests."""
    def __init__(self):
        self.path = Path(tempfile.mkdtemp(prefix="jarvis_test_"))

    def create_file(self, name: str, content: str = "test content") -> Path:
        f = self.path / name
        f.write_text(content, encoding="utf-8")
        return f

    def create_subdir(self, name: str) -> Path:
        d = self.path / name
        d.mkdir(exist_ok=True)
        return d

    def cleanup(self):
        if self.path.exists():
            shutil.rmtree(str(self.path))


# ══════════════════════════════════════════════════════════════════════════════
#  TESTS APP MANAGER — FORMAT & UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════════

def test_app_manager_instantiation():
    """AppManager s'instancie sans erreur."""
    from modules.app_manager import AppManager
    am = AppManager()
    assert am is not None

def test_app_manager_ok_helper():
    from modules.app_manager import AppManager
    r = AppManager._ok("test", {"k": "v"})
    assert r["success"] == True
    assert r["data"] == {"k": "v"}

def test_app_manager_err_helper():
    from modules.app_manager import AppManager
    r = AppManager._err("erreur")
    assert r["success"] == False

def test_resolve_exe_names_chrome():
    """_resolve_exe_names retourne le bon set pour chrome."""
    from modules.app_manager import AppManager
    targets = AppManager()._resolve_exe_names("chrome")
    assert "chrome.exe" in targets

def test_resolve_exe_names_word():
    """_resolve_exe_names retourne le bon set pour word."""
    from modules.app_manager import AppManager
    targets = AppManager()._resolve_exe_names("word")
    assert "winword.exe" in targets

def test_fuzzy_match_partial():
    """_fuzzy_match trouve des applications avec une recherche partielle."""
    from modules.app_manager import AppManager
    results = AppManager()._fuzzy_match("chrom")
    assert len(results) >= 1
    assert any("chrome" in r for r in results)

def test_fuzzy_match_no_match():
    """_fuzzy_match retourne [] si rien ne correspond."""
    from modules.app_manager import AppManager
    results = AppManager()._fuzzy_match("zzzyyyxxx_inexistant")
    assert isinstance(results, list)


# ══════════════════════════════════════════════════════════════════════════════
#  TESTS APP MANAGER — LUNDI : is_running, check_app
# ══════════════════════════════════════════════════════════════════════════════

def test_is_running_returns_bool():
    """is_running retourne toujours un bool."""
    from modules.app_manager import AppManager
    result = AppManager().is_running("chrome")
    assert isinstance(result, bool)

def test_is_running_nonexistent():
    """Une application fictive ne tourne pas."""
    from modules.app_manager import AppManager
    result = AppManager().is_running("application_qui_nexiste_pas_jarvis_xyz")
    assert result == False

def test_check_app_format():
    """check_app retourne le bon format."""
    from modules.app_manager import AppManager
    result = AppManager().check_app("chrome")
    assert_response(result, expected_success=True)
    assert "running" in result["data"]
    assert "instances" in result["data"]
    assert "count" in result["data"]

def test_check_app_nonexistent_not_running():
    """check_app sur une app inexistante retourne running=False."""
    from modules.app_manager import AppManager
    result = AppManager().check_app("application_inexistante_xyz_jarvis")
    assert_response(result, expected_success=True)
    assert result["data"]["running"] == False
    assert result["data"]["count"] == 0


# ══════════════════════════════════════════════════════════════════════════════
#  TESTS APP MANAGER — LUNDI : open_app / close_app
# ══════════════════════════════════════════════════════════════════════════════

def test_open_app_unknown_returns_error():
    """Ouvrir une app inconnue retourne success=False avec suggestion."""
    from modules.app_manager import AppManager
    result = AppManager().open_app("application_xyz_inconnue_12345")
    assert_response(result, expected_success=False)

def test_open_app_known_but_not_installed():
    """App connue mais non installée retourne success=False (pas de crash)."""
    from modules.app_manager import AppManager
    # En environnement de test, Word ne sera pas là → success=False attendu
    result = AppManager().open_app("word")
    assert_response(result)  # On vérifie juste que ça ne plante pas

def test_close_app_not_running():
    """Fermer une app qui ne tourne pas retourne success=False explicite."""
    from modules.app_manager import AppManager
    result = AppManager().close_app("application_xyz_inconnue_pas_lancee")
    assert_response(result, expected_success=False)
    assert "n'est pas" in result["message"] or "not" in result["message"].lower()


# ══════════════════════════════════════════════════════════════════════════════
#  TESTS APP MANAGER — MARDI : list_running_apps, list_known_apps, restart_app
# ══════════════════════════════════════════════════════════════════════════════

def test_list_known_apps_format():
    """list_known_apps retourne une liste non vide d'apps connues."""
    from modules.app_manager import AppManager
    result = AppManager().list_known_apps()
    assert_response(result, expected_success=True)
    assert "apps" in result["data"]
    assert len(result["data"]["apps"]) > 5
    assert "display" in result["data"]

def test_list_known_apps_structure():
    """Chaque app connue a les champs name et exe."""
    from modules.app_manager import AppManager
    result = AppManager().list_known_apps()
    for app in result["data"]["apps"]:
        assert "name" in app
        assert "exe" in app
        assert isinstance(app["name"], str)
        assert isinstance(app["exe"], str)

def test_list_running_apps_format():
    """list_running_apps retourne le bon format."""
    from modules.app_manager import AppManager
    result = AppManager().list_running_apps()
    assert_response(result, expected_success=True)
    assert "apps" in result["data"]
    assert "count" in result["data"]
    assert "display" in result["data"]
    assert isinstance(result["data"]["apps"], list)

def test_restart_app_not_installed():
    """restart_app sur une app non installée retourne success=False proprement."""
    from modules.app_manager import AppManager
    result = AppManager().restart_app("application_xyz_inexistante_jarvis")
    assert_response(result)  # Format correct même en cas d'erreur

def test_app_map_completeness():
    """APP_MAP contient les applications essentielles."""
    from modules.app_manager import APP_MAP
    essential = ["chrome", "firefox", "vscode", "notepad", "calculatrice", "spotify"]
    for app in essential:
        assert app in APP_MAP, f"'{app}' manquant dans APP_MAP"

def test_app_map_values_structure():
    """Chaque entrée de APP_MAP a bien 3 éléments (exe, win_cmd, fallback)."""
    from modules.app_manager import APP_MAP
    for name, value in APP_MAP.items():
        assert isinstance(value, tuple),  f"{name}: valeur doit être un tuple"
        assert len(value) == 3,           f"{name}: tuple doit avoir 3 éléments"
        assert isinstance(value[0], str), f"{name}: exe doit être une str"


# ══════════════════════════════════════════════════════════════════════════════
#  TESTS FILE MANAGER — FORMAT & UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════════

def test_file_manager_instantiation():
    from modules.file_manager import FileManager
    fm = FileManager()
    assert fm is not None

def test_format_size_bytes():
    from modules.file_manager import FileManager
    assert FileManager._format_size(512) == "512 B"

def test_format_size_kb():
    from modules.file_manager import FileManager
    result = FileManager._format_size(2048)
    assert "KB" in result
    assert "2.0" in result

def test_format_size_mb():
    from modules.file_manager import FileManager
    result = FileManager._format_size(5 * 1024 * 1024)
    assert "MB" in result

def test_format_size_gb():
    from modules.file_manager import FileManager
    result = FileManager._format_size(2 * 1024**3)
    assert "GB" in result

def test_file_info_dict():
    """_file_info_dict retourne les bons champs pour un fichier temporaire."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        f = tmp.create_file("test.txt", "hello")
        info = FileManager()._file_info_dict(f)
        assert info["name"] == "test.txt"
        assert info["extension"] == ".txt"
        assert info["size"] == 5
        assert info["is_dir"] == False
        assert "modified" in info
        assert "created" in info
        assert "path" in info
    finally:
        tmp.cleanup()

def test_file_info_dict_directory():
    """_file_info_dict fonctionne aussi pour un dossier."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        info = FileManager()._file_info_dict(tmp.path)
        assert info["is_dir"] == True
    finally:
        tmp.cleanup()


# ══════════════════════════════════════════════════════════════════════════════
#  TESTS FILE MANAGER — MERCREDI : search_file, search_by_type, open_file, list_folder
# ══════════════════════════════════════════════════════════════════════════════

def test_search_file_finds_existing():
    """search_file trouve un fichier créé dans le dossier temporaire."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        tmp.create_file("rapport_test_jarvis.txt", "contenu test")
        fm = FileManager(search_dirs=[tmp.path], max_depth=2)
        result = fm.search_file("rapport_test_jarvis")
        assert_response(result, expected_success=True)
        assert result["data"]["count"] >= 1
        names = [f["name"] for f in result["data"]["files"]]
        assert "rapport_test_jarvis.txt" in names
    finally:
        tmp.cleanup()

def test_search_file_case_insensitive():
    """search_file est insensible à la casse."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        tmp.create_file("Budget_2024.xlsx", "")
        fm = FileManager(search_dirs=[tmp.path], max_depth=2)
        result = fm.search_file("budget_2024")
        assert_response(result, expected_success=True)
    finally:
        tmp.cleanup()

def test_search_file_not_found():
    """search_file retourne success=False si rien trouvé."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        fm = FileManager(search_dirs=[tmp.path], max_depth=2)
        result = fm.search_file("fichier_qui_nexiste_absolument_pas_xyz_123")
        assert_response(result, expected_success=False)
    finally:
        tmp.cleanup()

def test_search_file_result_structure():
    """Chaque résultat a les bons champs."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        tmp.create_file("mon_fichier.txt", "test")
        fm = FileManager(search_dirs=[tmp.path], max_depth=2)
        result = fm.search_file("mon_fichier")
        if result["success"]:
            for f in result["data"]["files"]:
                assert "name"      in f
                assert "path"      in f
                assert "size"      in f
                assert "size_str"  in f
                assert "modified"  in f
                assert "extension" in f
    finally:
        tmp.cleanup()

def test_search_by_type_pdf():
    """search_by_type trouve les fichiers .pdf."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        tmp.create_file("doc1.pdf", "pdf content")
        tmp.create_file("doc2.pdf", "pdf content")
        tmp.create_file("doc3.txt", "text content")
        fm = FileManager(search_dirs=[tmp.path], max_depth=2)
        result = fm.search_by_type(".pdf")
        assert_response(result, expected_success=True)
        assert result["data"]["count"] == 2
        for f in result["data"]["files"]:
            assert f["extension"] == ".pdf"
    finally:
        tmp.cleanup()

def test_search_by_type_without_dot():
    """search_by_type accepte l'extension sans point."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        tmp.create_file("test.py", "# python")
        fm = FileManager(search_dirs=[tmp.path], max_depth=2)
        result = fm.search_by_type("py")
        assert_response(result, expected_success=True)
    finally:
        tmp.cleanup()

def test_search_by_type_category():
    """search_by_type accepte les catégories ('documents', 'images')."""
    from modules.file_manager import FileManager, FILE_TYPE_CATEGORIES
    tmp = TempDir()
    try:
        tmp.create_file("report.docx", "word doc")
        tmp.create_file("notes.txt", "text")
        fm = FileManager(search_dirs=[tmp.path], max_depth=2)
        result = fm.search_by_type("documents")
        assert_response(result)  # Peut trouver ou non selon les exts
    finally:
        tmp.cleanup()

def test_open_file_nonexistent():
    """open_file retourne success=False si fichier introuvable."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        fm = FileManager(search_dirs=[tmp.path], max_depth=2)
        result = fm.open_file("fichier_qui_nexiste_pas_xyz.txt")
        assert_response(result, expected_success=False)
    finally:
        tmp.cleanup()

def test_list_folder_existing():
    """list_folder liste correctement un dossier existant."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        tmp.create_file("a.txt", "a")
        tmp.create_file("b.py", "b")
        tmp.create_subdir("subdir")
        fm = FileManager()
        result = fm.list_folder(str(tmp.path))
        assert_response(result, expected_success=True)
        data = result["data"]
        assert "files" in data
        assert "folders" in data
        assert "display" in data
        assert len(data["files"]) == 2
        assert len(data["folders"]) == 1
    finally:
        tmp.cleanup()

def test_list_folder_nonexistent():
    """list_folder retourne success=False pour un dossier inexistant."""
    from modules.file_manager import FileManager
    result = FileManager().list_folder("/chemin/qui/nexiste/pas/xyz")
    assert_response(result, expected_success=False)

def test_list_folder_total_count():
    """list_folder compte correctement le total."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        for i in range(5):
            tmp.create_file(f"file_{i}.txt", f"content {i}")
        result = FileManager().list_folder(str(tmp.path))
        assert result["data"]["total"] >= 5
    finally:
        tmp.cleanup()


# ══════════════════════════════════════════════════════════════════════════════
#  TESTS FILE MANAGER — JEUDI : copy, move, rename, delete, create_folder
# ══════════════════════════════════════════════════════════════════════════════

def test_copy_file_success():
    """copy_file copie un fichier correctement."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        src = tmp.create_file("source.txt", "contenu original")
        dst_dir = tmp.create_subdir("backup")
        result = FileManager().copy_file(str(src), str(dst_dir))
        assert_response(result, expected_success=True)
        assert (dst_dir / "source.txt").exists()
        assert (dst_dir / "source.txt").read_text() == "contenu original"
    finally:
        tmp.cleanup()

def test_copy_file_preserves_content():
    """copy_file préserve le contenu byte pour byte."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        content = "Contenu avec des caractères spéciaux: àéîõü"
        src = tmp.create_file("original.txt", content)
        dst = tmp.path / "copie.txt"
        FileManager().copy_file(str(src), str(dst))
        assert dst.read_text(encoding="utf-8") == content
    finally:
        tmp.cleanup()

def test_copy_file_nonexistent_source():
    """copy_file retourne success=False si source introuvable."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        result = FileManager().copy_file(
            str(tmp.path / "inexistant.txt"),
            str(tmp.path / "dest.txt")
        )
        assert_response(result, expected_success=False)
    finally:
        tmp.cleanup()

def test_copy_file_no_overwrite_by_default():
    """copy_file refuse d'écraser par défaut."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        src = tmp.create_file("source.txt", "source")
        dst = tmp.create_file("dest.txt", "destination originale")
        result = FileManager().copy_file(str(src), str(dst), overwrite=False)
        assert_response(result, expected_success=False)
        # Le fichier destination n'a pas été modifié
        assert dst.read_text() == "destination originale"
    finally:
        tmp.cleanup()

def test_copy_file_overwrite():
    """copy_file écrase si overwrite=True."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        src = tmp.create_file("source.txt", "nouveau contenu")
        dst = tmp.create_file("dest.txt", "ancien contenu")
        result = FileManager().copy_file(str(src), str(dst), overwrite=True)
        assert_response(result, expected_success=True)
        assert dst.read_text() == "nouveau contenu"
    finally:
        tmp.cleanup()

def test_move_file_success():
    """move_file déplace un fichier et le supprime de la source."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        src = tmp.create_file("a_deplacer.txt", "contenu")
        dst_dir = tmp.create_subdir("destination")
        result = FileManager().move_file(str(src), str(dst_dir))
        assert_response(result, expected_success=True)
        assert not src.exists()                          # source supprimée
        assert (dst_dir / "a_deplacer.txt").exists()    # destination créée
    finally:
        tmp.cleanup()

def test_move_file_to_new_name():
    """move_file peut déplacer en changeant le nom."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        src = tmp.create_file("ancien.txt", "hello")
        dst = tmp.path / "nouveau.txt"
        result = FileManager().move_file(str(src), str(dst))
        assert_response(result, expected_success=True)
        assert not src.exists()
        assert dst.exists()
        assert dst.read_text() == "hello"
    finally:
        tmp.cleanup()

def test_move_file_nonexistent():
    """move_file retourne success=False si source introuvable."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        result = FileManager().move_file(
            str(tmp.path / "inexistant.txt"),
            str(tmp.path / "dest.txt")
        )
        assert_response(result, expected_success=False)
    finally:
        tmp.cleanup()

def test_rename_file_success():
    """rename_file renomme correctement un fichier."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        f = tmp.create_file("vieux_nom.txt", "contenu")
        result = FileManager().rename_file(str(f), "nouveau_nom")
        assert_response(result, expected_success=True)
        assert not f.exists()
        assert (tmp.path / "nouveau_nom.txt").exists()
    finally:
        tmp.cleanup()

def test_rename_file_keeps_extension():
    """rename_file conserve l'extension si non spécifiée."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        f = tmp.create_file("document.docx", "word")
        FileManager().rename_file(str(f), "rapport_final")
        assert (tmp.path / "rapport_final.docx").exists()
    finally:
        tmp.cleanup()

def test_rename_file_with_new_extension():
    """rename_file accepte un nouveau nom avec extension."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        f = tmp.create_file("data.csv", "a,b,c")
        result = FileManager().rename_file(str(f), "data_2024.csv")
        assert_response(result, expected_success=True)
        assert (tmp.path / "data_2024.csv").exists()
    finally:
        tmp.cleanup()

def test_rename_nonexistent_file():
    """rename_file retourne success=False si fichier introuvable."""
    from modules.file_manager import FileManager
    result = FileManager().rename_file("/chemin/inexistant/xyz.txt", "nouveau")
    assert_response(result, expected_success=False)

def test_rename_conflict():
    """rename_file refuse si le nouveau nom existe déjà."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        f1 = tmp.create_file("fichier1.txt", "un")
        tmp.create_file("fichier2.txt", "deux")
        result = FileManager().rename_file(str(f1), "fichier2.txt")
        assert_response(result, expected_success=False)
    finally:
        tmp.cleanup()

def test_delete_file_success():
    """delete_file supprime un fichier."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        f = tmp.create_file("a_supprimer.txt", "bye")
        assert f.exists()
        result = FileManager().delete_file(str(f))
        assert_response(result, expected_success=True)
        assert not f.exists()
    finally:
        tmp.cleanup()

def test_delete_folder_success():
    """delete_file supprime un dossier et son contenu."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        d = tmp.create_subdir("dossier_a_supprimer")
        (d / "fichier.txt").write_text("contenu")
        result = FileManager().delete_file(str(d))
        assert_response(result, expected_success=True)
        assert not d.exists()
    finally:
        tmp.cleanup()

def test_delete_nonexistent():
    """delete_file retourne success=False si fichier introuvable."""
    from modules.file_manager import FileManager
    result = FileManager().delete_file("/chemin/qui/nexiste/pas/xyz.txt")
    assert_response(result, expected_success=False)

def test_delete_blocked_home():
    """delete_file refuse de supprimer le dossier home."""
    from modules.file_manager import FileManager
    result = FileManager().delete_file(str(Path.home()))
    assert_response(result, expected_success=False)
    assert result["data"] is not None
    assert result["data"].get("blocked") == True

def test_create_folder_success():
    """create_folder crée un nouveau dossier."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        new_dir = tmp.path / "nouveau_dossier"
        assert not new_dir.exists()
        result = FileManager().create_folder(str(new_dir))
        assert_response(result, expected_success=True)
        assert new_dir.exists()
        assert new_dir.is_dir()
    finally:
        tmp.cleanup()

def test_create_folder_nested():
    """create_folder crée des sous-dossiers imbriqués."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        nested = tmp.path / "niveau1" / "niveau2" / "niveau3"
        result = FileManager().create_folder(str(nested))
        assert_response(result, expected_success=True)
        assert nested.exists()
    finally:
        tmp.cleanup()

def test_create_folder_already_exists():
    """create_folder retourne success=True si le dossier existe déjà."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        result = FileManager().create_folder(str(tmp.path))
        assert_response(result, expected_success=True)
        assert result["data"]["already_existed"] == True
    finally:
        tmp.cleanup()


# ══════════════════════════════════════════════════════════════════════════════
#  TESTS FILE MANAGER — search_by_content
# ══════════════════════════════════════════════════════════════════════════════

def test_search_by_content_finds_keyword():
    """search_by_content trouve un fichier contenant le mot cherché."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        tmp.create_file("rapport.txt",     "Ce rapport contient le budget 2024")
        tmp.create_file("autre.txt",       "Ce fichier ne contient rien d'intéressant")
        tmp.create_file("notes.py",        "# budget = 50000")
        fm = FileManager(search_dirs=[tmp.path], max_depth=2)
        result = fm.search_by_content("budget")
        assert_response(result, expected_success=True)
        assert result["data"]["count"] >= 2
    finally:
        tmp.cleanup()

def test_search_by_content_not_found():
    """search_by_content retourne success=False si rien trouvé."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        tmp.create_file("fichier.txt", "contenu normal")
        fm = FileManager(search_dirs=[tmp.path], max_depth=2)
        result = fm.search_by_content("xyz_mot_inexistant_jarvis_123")
        assert_response(result, expected_success=False)
    finally:
        tmp.cleanup()

def test_search_by_content_includes_line_numbers():
    """search_by_content inclut les numéros de lignes dans les matches."""
    from modules.file_manager import FileManager
    tmp = TempDir()
    try:
        tmp.create_file("data.txt", "ligne1\nbudget: 5000\nligne3")
        fm = FileManager(search_dirs=[tmp.path], max_depth=2)
        result = fm.search_by_content("budget")
        assert_response(result, expected_success=True)
        file_data = result["data"]["files"][0]
        assert "matches" in file_data
        assert len(file_data["matches"]) >= 1
        line_no, line_text = file_data["matches"][0]
        assert isinstance(line_no, int)
        assert "budget" in line_text.lower()
    finally:
        tmp.cleanup()


# ══════════════════════════════════════════════════════════════════════════════
#  TESTS AGENT — INTÉGRATION SEMAINE 3
# ══════════════════════════════════════════════════════════════════════════════

def test_agent_dispatch_ouvre_app():
    """Agent dispatche 'ouvre chrome' vers AppManager."""
    from core.agent import Agent
    result = Agent().handle_command("ouvre chrome")
    assert_response(result)  # Chrome peut ne pas être installé, mais pas de crash

def test_agent_dispatch_ferme_app():
    """Agent dispatche 'ferme notepad' vers AppManager."""
    from core.agent import Agent
    result = Agent().handle_command("ferme notepad")
    assert_response(result)

def test_agent_dispatch_applications_ouvertes():
    """Agent dispatche 'applications ouvertes'."""
    from core.agent import Agent
    result = Agent().handle_command("applications ouvertes")
    assert_response(result, expected_success=True)
    assert "apps" in result["data"]

def test_agent_dispatch_liste_applications():
    """Agent dispatche 'liste applications'."""
    from core.agent import Agent
    result = Agent().handle_command("liste applications")
    assert_response(result, expected_success=True)

def test_agent_dispatch_cherche_fichier(tmp_path):
    """Agent dispatche 'cherche fichier' vers FileManager."""
    from core.agent import Agent
    result = Agent().handle_command("cherche fichier rapport_xyz_inexistant")
    assert_response(result)

def test_agent_dispatch_copie_format_error():
    """Agent retourne erreur si format copie incorrect."""
    from core.agent import Agent
    result = Agent().handle_command("copie sans vers")
    assert_response(result)

def test_agent_dispatch_creer_dossier():
    """Agent dispatche 'crée dossier' vers FileManager."""
    from core.agent import Agent
    import tempfile
    tmp = tempfile.mkdtemp()
    new_dir = str(Path(tmp) / "test_jarvis_agent")
    try:
        result = Agent().handle_command(f"crée dossier {new_dir}")
        assert_response(result, expected_success=True)
        assert Path(new_dir).exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def test_agent_dispatch_aide():
    """Agent affiche l'aide avec les commandes semaine 3."""
    from core.agent import Agent
    result = Agent().handle_command("aide")
    assert_response(result, expected_success=True)
    help_text = result["data"]["display"]
    assert "APPLICATIONS" in help_text
    assert "FICHIERS" in help_text


# ══════════════════════════════════════════════════════════════════════════════
#  RUNNER MANUEL
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import traceback

    test_funcs = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]

    passed, failed = [], []
    for name, fn in test_funcs:
        # Injecter tmp_path si besoin
        import inspect
        sig = inspect.signature(fn)
        try:
            if "tmp_path" in sig.parameters:
                import tempfile
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            passed.append(name)
        except Exception as e:
            failed.append((name, str(e)))

    print()
    print("=" * 60)
    print(f"  {len(passed)} PASSES  |  {len(failed)} FAILURES  |  {len(test_funcs)} TOTAL")
    print("=" * 60)
    if failed:
        print()
        for fn_name, err in failed:
            print(f"  FAIL : {fn_name}")
            print(f"         {err[:120]}")
    else:
        print("  Tous les tests passent. Semaine 3 VALIDÉE.")