#!/usr/bin/env python3
# <xbar.title>Claude Agents Bar</xbar.title>
# <xbar.title.ru>Claude Agents Bar</xbar.title.ru>
# <xbar.title.zh>Claude 代理栏</xbar.title.zh>
# <xbar.title.fr>Claude Agents Bar</xbar.title.fr>
# <xbar.title.de>Claude Agents Bar</xbar.title.de>
# <xbar.title.it>Claude Agents Bar</xbar.title.it>
# <xbar.title.vi>Claude Agents Bar</xbar.title.vi>
# <xbar.version>1.0.0</xbar.version>
# <xbar.author>Alexey Krylov</xbar.author>
# <xbar.author.github>alexey-krylov/ClaudeAgentsBar</xbar.author.github>
# <xbar.desc>Live status of Claude Code sessions across all projects</xbar.desc>
# <xbar.desc.ru>Статус сессий Claude Code в реальном времени по всем проектам</xbar.desc.ru>
# <xbar.desc.zh>在所有项目中实时显示 Claude Code 会话状态</xbar.desc.zh>
# <xbar.desc.fr>État en direct des sessions Claude Code sur tous les projets</xbar.desc.fr>
# <xbar.desc.de>Live-Status der Claude-Code-Sitzungen aller Projekte</xbar.desc.de>
# <xbar.desc.it>Stato live delle sessioni Claude Code in tutti i progetti</xbar.desc.it>
# <xbar.desc.vi>Trạng thái phiên Claude Code trên mọi dự án theo thời gian thực</xbar.desc.vi>
# <xbar.dependencies>python3</xbar.dependencies>
# <xbar.abouturl>https://github.com/alexey-krylov/ClaudeAgentsBar</xbar.abouturl>
# <swiftbar.hideAbout>false</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>false</swiftbar.hideDisablePlugin>

"""SwiftBar entry point — a thin shim over the :mod:`claude_agents_bar` package.

SwiftBar dictates the ``<name>.<interval><unit>.<ext>`` filename, which isn't
a legal Python module identifier, so the actual implementation lives one
directory below in the regular package next to this file. This script does
exactly three things: make that package importable, dispatch to ``main()``,
and propagate the exit code.

``Path(__file__).resolve()`` follows the install-time symlink so the
sibling ``claude_agents_bar/`` directory is what gets added to ``sys.path``
even when SwiftBar runs us out of its plugins folder. See PLUGIN.md and
ADR-0006 for why the implementation is forced to be stdlib-only Python 3.9.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_agents_bar import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
