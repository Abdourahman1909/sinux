"""Sinux shell composition for the agent preview: stock KDE Plasma 6 + Breeze,
with Sinux's island, pointer, voice and Settings on top.

Plasma supplies the maintained desktop furniture (floating panel, Kickoff,
icons-only task manager, system tray, clock, notifications) and KWin's Breeze
window decoration. This undoes the lab's custom colours, panel artwork and
window frame so the base looks and behaves like Plasma itself.
"""
import json
from pathlib import Path
import shutil

STOCK_SHELL = Path('/usr/share/plasma/shells/org.kde.plasma.desktop')

LAYOUT = """// SPDX-License-Identifier: GPL-3.0-only
// Sinux on Plasma 6: one floating panel at the bottom (sized to its content),
// nothing at the top so the Sinux Island has the top centre to itself.
var desktop = desktops()[0];
desktop.wallpaperPlugin = "org.kde.image";
desktop.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
desktop.writeConfig("Image", "file://WALLPAPER");

var panel = new Panel;
panel.location = "bottom";
panel.height = 52;
panel.floating = true;
panel.lengthMode = "fit";
panel.alignment = "center";
panel.hiding = "none";
var kickoff = panel.addWidget("org.kde.plasma.kickoff");
kickoff.currentConfigGroup = ["General"];
kickoff.writeConfig("icon", "view-app-grid-symbolic");
var tasks = panel.addWidget("org.kde.plasma.icontasks");
tasks.currentConfigGroup = ["General"];
tasks.writeConfig("launchers", ["applications:org.kde.dolphin.desktop", "applications:org.kde.konsole.desktop",
    "applications:org.kde.kate.desktop", "applications:org.kde.okular.desktop", "applications:org.kde.gwenview.desktop",
    "applications:org.kde.kcalc.desktop", "file://SETTINGS_ENTRY"]);
panel.addWidget("org.kde.plasma.marginsseparator");
panel.addWidget("org.kde.plasma.systemtray");
var clock = panel.addWidget("org.kde.plasma.digitalclock");
clock.currentConfigGroup = ["Appearance"];
clock.writeConfig("showDate", true);
clock.writeConfig("dateFormat", "shortDate");
"""

SETTINGS_DESKTOP = ("[Desktop Entry]\nType=Application\nName=Sinux Settings\nGenericName=Assistant settings\n"
                    "Comment=Connect Sinux, choose the talk key, voice and permissions\nIcon=preferences-system\n"
                    "Keywords=sinux;assistant;ai;voice;settings;\nCategories=Settings;\n"
                    "Exec=gdbus call --session --dest org.sinux.Agent --object-path /org/sinux/Agent "
                    "--method org.sinux.Agent1.OpenSettings\n")


def assemble(base):
    base = Path(base)
    shell = base / 'data/plasma/shells/org.sinux.lab'
    # Stock panel artwork (translucent Breeze panel), stock desktop containment.
    shutil.copyfile(STOCK_SHELL / 'contents/views/Panel.qml', shell / 'contents/views/Panel.qml')
    defaults = shell / 'contents/defaults'
    defaults.write_text(defaults.read_text().replace('Containment=org.sinux.visualdesktop', 'Containment=org.kde.plasma.folder'))
    wallpaper = base / 'data/plasma/plasmoids/org.sinux.visualdesktop/landscape.png'
    settings_entry = base / 'data/applications/org.sinux.settings.desktop'
    (shell / 'contents/layout.js').write_text(LAYOUT.replace('WALLPAPER', str(wallpaper))
                                              .replace('SETTINGS_ENTRY', str(settings_entry)))
    settings_entry.parent.mkdir(parents=True, exist_ok=True)
    settings_entry.write_text(SETTINGS_DESKTOP)
    config = base / 'config'
    # Breeze Light colours, Breeze Plasma theme, Breeze window decoration.
    breeze = Path('/usr/share/color-schemes/BreezeLight.colors').read_text()
    breeze = breeze.replace('[General]', '[General]\nColorScheme=BreezeLight\nfont=Noto Sans,10,-1,5,400,0,0,0,0,0,0,0,0,0,0,1', 1)
    (config / 'kdeglobals').write_text(breeze + '\n[KDE]\nLookAndFeelPackage=org.kde.breeze.desktop\nwidgetStyle=Breeze\n'
                                       '\n[Icons]\nTheme=breeze\n')
    # Breeze relies on KWin's blur behind translucent menus/panels; the preview has none, so
    # windows would show through Kickoff. Use Breeze's own opaque artwork (same design, solid).
    theme = base / 'data/plasma/desktoptheme/sinux-breeze'
    if theme.exists(): shutil.rmtree(theme)
    shutil.copytree('/usr/share/plasma/desktoptheme/default', theme)
    for item in (theme / 'opaque').rglob('*'):
        if item.is_file():
            target = theme / item.relative_to(theme / 'opaque')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, target)
    shutil.rmtree(theme / 'translucent', ignore_errors=True)
    meta = json.loads((theme / 'metadata.json').read_text())
    meta['KPlugin'].update(Id='sinux-breeze', Name='Breeze (solid, Sinux preview)')
    (theme / 'metadata.json').write_text(json.dumps(meta))
    (config / 'plasmarc').write_text('[Theme]\nname=sinux-breeze\n')
    (config / 'kwinrc').write_text('[org.kde.kdecoration2]\nlibrary=org.kde.breeze\ntheme=Breeze\n'
                                   'ButtonsOnLeft=\nButtonsOnRight=IAX\n')
