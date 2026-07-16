import time
import re
from typing import List, Dict, Optional
from playwright.sync_api import sync_playwright

def _normalize_text(text: str) -> str:
    if not text:
        return ""
    # Entfernt ALLES außer Buchstaben und Zahlen für einen bombensicheren Vergleich
    return re.sub(r'[^a-z0-9]', '', str(text).lower())

class FSGBrowser:
    def __init__(self, config):
        self.config = config
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=False)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.browser:
            self.browser.close()
        if self.pw:
            self.pw.stop()

    def login(self):
        if not self.config.username or not self.config.password:
            return False
        self.page.goto(self.config.login_url)
        self.page.fill("#tx-felogin-input-username", self.config.username)
        self.page.fill("#tx-felogin-input-password", self.config.password)
        self.page.click('input[name="submit"]')
        self.page.wait_for_load_state("networkidle")
        return True

    def goto_bom(self):
        self.page.goto(self.config.bom_url)

    def _force_system_change(self, target_sys_label: str) -> bool:
        target_norm = _normalize_text(target_sys_label)

        sys_options = self.page.locator("#DTE_Field_system option").evaluate_all(
            "els => els.map(e => ({text: e.textContent, value: e.value}))"
        )

        exact_sys_val = None
        dummy_val = None

        for opt in sys_options:
            if not opt['value']:
                continue
            if _normalize_text(opt['text']) == target_norm:
                exact_sys_val = opt['value']
            else:
                dummy_val = opt['value']

        if not exact_sys_val:
            return False

        sys_locator = self.page.locator("#DTE_Field_system")

        if dummy_val:
            sys_locator.select_option(value=dummy_val)
            self.page.evaluate('window.jQuery ? window.jQuery("#DTE_Field_system").trigger("change") : null')
            sys_locator.dispatch_event("change")
            self.page.wait_for_timeout(150)

        sys_locator.select_option(value=exact_sys_val)
        self.page.evaluate('window.jQuery ? window.jQuery("#DTE_Field_system").trigger("change") : null')
        sys_locator.dispatch_event("change")

        return True

    def _wait_for_assembly_list_to_settle(self, max_wait_ms: int = 6000, stable_checks: int = 2, interval_ms: int = 250) -> None:
        """
        Polls the Assembly dropdown until its option list stops changing
        between consecutive checks, instead of trusting a single fixed
        sleep. This is what protects against snapshotting a stale (wrong
        system's) options list when the site's AJAX refresh happens to be
        slower than usual (e.g. under load / when the script runs fast).
        """
        deadline = time.time() + (max_wait_ms / 1000)
        last = None
        stable = 0
        while time.time() < deadline:
            current = self.page.locator("#DTE_Field_assembly option").evaluate_all(
                "els => els.map(e => e.textContent)"
            )
            if current and current == last:
                stable += 1
                if stable >= stable_checks:
                    return
            else:
                stable = 0
            last = current
            self.page.wait_for_timeout(interval_ms)

    def fetch_site_options(self, system_label: Optional[str] = None) -> List[str]:
        try:
            self.page.get_by_text("New", exact=True).first.click(force=True)
            self.page.wait_for_selector(".DTE_Action_Create", state="visible", timeout=5000)
            self.page.wait_for_timeout(200)

            if system_label:
                if not self._force_system_change(system_label):
                    return []
                self._wait_for_assembly_list_to_settle()

            options_data = self.page.locator("#DTE_Field_assembly option").evaluate_all(
                "els => els.map(e => e.textContent)"
            )

            options = [o.strip() for o in options_data if o and o.strip()]
            self.page.keyboard.press("Escape")
            return options
        except Exception:
            return []

    def scrape_existing_parts(self, matcher) -> Dict[str, Dict]:
        try:
            self.page.wait_for_selector("#bom-table", timeout=10000)

            try:
                if self.page.locator("select[name='bom-table_length']").is_visible():
                    self.page.locator("select[name='bom-table_length']").select_option("-1")
                    time.sleep(3.0)
            except Exception:
                pass

            data = self.page.evaluate("""() => {
                const results = [];
                const table = document.querySelector('#bom-table');
                if (!table) return [];

                const ths = Array.from(table.querySelectorAll('thead th'));
                const headers = ths.map(th => th.innerText.toLowerCase().trim());

                const findIdx = (aliases) => {
                    return headers.findIndex(h => aliases.some(a => h.includes(a)));
                };

                const idxMap = {
                    system: findIdx(['system', 'sys']),
                    assembly: findIdx(['assembly', 'asm', 'assy']),
                    part: findIdx(['part', 'name', 'designation', 'description']),
                    id_col: findIdx(['id', 'part-id']),
                    custom_id: findIdx(['custom', 'part no', 'part-no']),
                };

                let lastSystem = "";
                let lastAssembly = "";

                const rows = table.querySelectorAll('tbody tr');
                rows.forEach(tr => {
                    if (tr.classList.contains('empty') || tr.innerText.includes('No data')) return;

                    const cells = tr.querySelectorAll('td');
                    if (cells.length < 4) return;

                    let currentSys = (idxMap.system !== -1 && cells[idxMap.system]) ? cells[idxMap.system].innerText.trim() : "";
                    let currentAsm = (idxMap.assembly !== -1 && cells[idxMap.assembly]) ? cells[idxMap.assembly].innerText.trim() : "";
                    let currentPart = (idxMap.part !== -1 && cells[idxMap.part]) ? cells[idxMap.part].innerText.trim() : "";
                    let currentIdVal = (idxMap.id_col !== -1 && cells[idxMap.id_col]) ? cells[idxMap.id_col].innerText.trim() : "";
                    let currentCustomId = (idxMap.custom_id !== -1 && cells[idxMap.custom_id]) ? cells[idxMap.custom_id].innerText.trim() : "";

                    if (!currentPart && tr.id.startsWith('bompart_')) {
                         for (let i = Math.max(idxMap.assembly, 2) + 1; i < cells.length; i++) {
                             if (!cells[i]) continue;
                             const txt = cells[i].innerText.trim();
                             if (txt && txt.length > 1) {
                                 currentPart = txt;
                                 break;
                             }
                         }
                    }

                    if (currentSys && currentSys.length > 1) lastSystem = currentSys;
                    if (currentAsm && currentAsm.length > 1) lastAssembly = currentAsm;

                    if (tr.id && tr.id.startsWith('bompart_')) {
                        results.push({
                            row_id: tr.id,
                            site_id: currentIdVal,
                            system: lastSystem,
                            assembly: lastAssembly,
                            part: currentPart,
                            custom_id: currentCustomId
                        });
                    }
                });
                return results;
            }""")

            existing = {}
            for r in data:
                sys = ""
                if r.get('system'):
                    sys = r['system'].split(' ')[0].strip().upper()

                if not sys and r.get('site_id'):
                    parts = str(r['site_id']).split('-')
                    if len(parts) >= 2:
                        sys = parts[1].upper()

                if not sys and r.get('row_id'):
                    row_id_parts = str(r['row_id']).split('_')
                    if len(row_id_parts) >= 2 and len(row_id_parts[0]) <= 3:
                        sys = row_id_parts[0].upper()

                key = matcher.canonical_key(sys, r.get('assembly') or "", r.get('part') or "")
                existing[key] = r

            return existing
        except Exception as e:
            print(f"Error scraping existing parts: {e}")
            return {}

    def _fill_if_present(self, selector: str, value: str) -> bool:
        """
        Fills `selector` with `value` only if the field actually exists and
        is visible on the current form. The Costs/Emissions fields are new
        this year and don't necessarily appear for every system / make-buy
        combination, so we must not blow up (or silently rely on a field
        that isn't there) when they're absent.
        """
        if not value:
            return False
        loc = self.page.locator(selector)
        try:
            if loc.count() == 0 or not loc.is_visible():
                return False
        except Exception:
            return False
        loc.fill(value)
        loc.dispatch_event("input")
        loc.dispatch_event("change")
        return True

    @staticmethod
    def _normalize_decimal(value: str) -> str:
        # German Excel files often use a comma as decimal separator (e.g.
        # "12,50"). If the value is *purely* numeric in that shape, convert
        # it to dot-decimal so the website's plain text cost field gets a
        # clean number. Anything that isn't a plain "digits,digits" value is
        # left untouched rather than guessed at.
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d+", value):
            return value.replace(".", "").replace(",", ".")
        if re.fullmatch(r"\d+,\d+", value):
            return value.replace(",", ".")
        return value

    def _safe_click_text(self, text: str, timeout: int = 5000, exact: bool = True) -> bool:
        """
        Clicks a button identified by its text, but only waits up to
        `timeout` (default 5s) and only if it actually exists right now.
        Plain Locator.click() with no explicit timeout falls back to
        Playwright's default of 30s - if the button we're looking for
        (typically "Cancel") has already disappeared because the dialog
        closed on its own in the meantime, that call just hangs for a full
        30s before failing. Returns True if a click was performed.
        """
        loc = self.page.get_by_text(text, exact=exact)
        try:
            if loc.count() == 0:
                return False
            loc.first.click(force=True, timeout=timeout)
            return True
        except Exception:
            return False

    def create_part(self, item: Dict):
        # 1. Reset Modal if open
        if self.page.locator(".DTE_Action_Create").is_visible():
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)
            if self.page.locator(".DTE_Action_Create").is_visible():
                self._safe_click_text("Cancel")
                self.page.wait_for_timeout(200)

        # Force=True überspringt Stabilitätschecks bei wackelnden Menüs
        self.page.get_by_text("New", exact=True).first.click(force=True, timeout=10000)
        self.page.wait_for_selector(".DTE_Action_Create", state="visible", timeout=10000)
        self.page.wait_for_timeout(200)

        target_sys_label = item['system_label']
        target_asm = item['assembly']

        # 2. System Wechsel (mit Fallback-"Bounce" gegen das Race-Condition-
        # Problem: ohne den erzwungenen Zwischenwechsel kann die Assembly-
        # Liste auf dem zuvor aktiven System stehen bleiben, z.B. "ASSI" aus
        # Autonomous, obwohl oben schon das gewünschte System steht).
        if not self._force_system_change(target_sys_label):
            raise RuntimeError(f"System '{target_sys_label}' existiert im Dropdown nicht.")

        target_asm_norm = _normalize_text(target_asm)
        exact_asm_val = None
        options_data = []

        for _ in range(30):
            options_data = self.page.locator("#DTE_Field_assembly option").evaluate_all(
                "els => els.map(e => ({text: e.textContent, value: e.value}))"
            )

            for opt in options_data:
                if not opt['value']:
                    continue
                if _normalize_text(opt['text']) == target_asm_norm:
                    exact_asm_val = opt['value']
                    break

            if exact_asm_val:
                break

            self.page.wait_for_timeout(200)

        if not exact_asm_val:
            clean_opts = [o['text'].strip() for o in options_data if o['text'] and o['text'].strip()]
            raise RuntimeError(f"Assembly '{target_asm}' im System '{target_sys_label}' nicht gefunden. Optionen: {clean_opts}")

        self.page.locator("#DTE_Field_assembly").select_option(value=exact_asm_val)
        self.page.locator("#DTE_Field_part").fill(item['part'])

        # 3. Make/Buy Logik
        is_buy = (item['makebuy'] == 'b')
        if is_buy:
            self.page.locator("#DTE_Field_makebuy_1").check()
        else:
            self.page.locator("#DTE_Field_makebuy_0").check()
        # Make/Buy kann abhängige Felder ein-/ausblenden (u.a. Costs/Emissions) -
        # der Seite kurz Zeit geben zu reagieren, bevor wir prüfen was sichtbar ist.
        self.page.wait_for_timeout(400)

        if item['comments']:
            self.page.locator("#DTE_Field_comments").fill(item['comments'])
        if item['quantity']:
            self.page.locator("#DTE_Field_quantity").fill(item['quantity'])

        # 4. Custom ID Logik
        if item.get('custom_id'):
            cloc = self.page.locator("#DTE_Field_part_no_custom")
            cloc.fill(item['custom_id'])

        # 5. Costs & Emissions (neu in diesem Jahr, erscheinen nicht bei
        # jedem System/Make-Buy - daher nur ausfüllen, wenn das Feld
        # tatsächlich vorhanden und sichtbar ist).
        raw_cost = self._normalize_decimal(str(item.get('costs') or "").strip())
        raw_emission = str(item.get('emissions') or "").strip()

        self._fill_if_present("#DTE_Field_sub_costs", raw_cost)
        self._fill_if_present("#DTE_Field_sub_comments_emissions", raw_emission)

        # Kleine Pause, damit JS die Eingaben sicher übernommen hat
        self.page.wait_for_timeout(500)

        # force=True erzwingt den Klick, selbst wenn der Button scheinbar nicht "stabil" ist
        self.page.get_by_text("Create", exact=True).first.click(force=True, timeout=10000)

        # 6. FAIL-FAST Polling Loop
        # Nutzt config.request_timeout (Standard 30s) statt einer fest
        # codierten Wartezeit - der Server kann gerade mit den neuen
        # Costs/Emissions-Validierungen mal länger brauchen ("processing").
        error_keywords = ("required", "too small", "too long", "invalid", "minimum", "maximum", "pflicht", "ungültig")
        max_wait_seconds = max(5.0, (self.config.request_timeout or 50000.0) / 1000.0)
        deadline = time.time() + max_wait_seconds
        success = False
        while time.time() < deadline:
            if not self.page.locator(".DTE_Action_Create").is_visible():
                success = True
                break

            errs = self.page.locator(".field-error, .help-block, [style*='color: red']").all_inner_texts()
            clean_errs = [e.strip() for e in errs if e.strip() and any(k in e.lower() for k in error_keywords)]

            if clean_errs:
                self._safe_click_text("Cancel")
                raise RuntimeError(f"Server hat Eingabe abgelehnt: {', '.join(clean_errs)}")

            self.page.wait_for_timeout(1000)

        if not success:
            # Letzter Check: das Fenster kann sich genau in diesem Moment
            # doch noch geschlossen haben (Server war nur langsam, Teil
            # wurde aber angelegt) - dann NICHT als Fehler werten und vor
            # allem nicht blind auf "Cancel" klicken, das dann nicht mehr
            # existiert und sonst weitere 30s blockieren würde.
            if not self.page.locator(".DTE_Action_Create").is_visible():
                success = True
            else:
                self._safe_click_text("Cancel")
                raise RuntimeError(
                    f"Timeout: Das Fenster hat sich nach {max_wait_seconds:.0f}s nicht geschlossen. "
                    f"Server reagiert nicht (REQUEST_TIMEOUT ggf. erhöhen)."
                )