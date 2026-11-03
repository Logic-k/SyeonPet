"""SyeonPet Document Exporter.

Exports assistant deliverables to TXT, Markdown, CSV (with Excel UTF-8 BOM),
HWP/Word-friendly HTML, and real HWPX (OWPML zip) for Hancom Word users.
"""
from __future__ import annotations

import csv
import html
import io
from pathlib import Path
import re
import zipfile


def export_to_txt(content: str, filepath: str | Path) -> Path:
    """Save content as plain text file in UTF-8."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def export_to_markdown(content: str, filepath: str | Path) -> Path:
    """Save content as markdown document in UTF-8."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def export_to_csv(content: str, filepath: str | Path) -> Path:
    """Extract tables or lines and export to CSV with UTF-8 BOM for Microsoft Excel."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[list[str]] = []
    # Check if content has markdown table lines
    for line in content.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("|") and trimmed.endswith("|"):
            parts = [p.strip() for p in trimmed.strip("|").split("|")]
            # Skip markdown table separator lines like :---
            if all(re.match(r"^:?-+:?$", p) for p in parts if p):
                continue
            rows.append(parts)
        elif trimmed and not rows:
            # Fallback line-by-line if no table found yet
            continue

    if not rows:
        # Fallback to single column lines
        rows = [[line] for line in content.splitlines() if line.strip()]

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for r in rows:
        writer.writerow(r)

    # Use utf-8-sig (with BOM) for seamless Korean support in Excel
    path.write_text(output.getvalue(), encoding="utf-8-sig")
    return path


def export_to_html(content: str, filepath: str | Path, title: str = "션 업무 산출물") -> Path:
    """Export to an HTML document optimized for Korean HWP and Word copy-paste."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    escaped_title = html.escape(title)
    # Simple line-by-line conversion with table preservation
    body_parts = []
    in_table = False

    for line in content.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("|") and trimmed.endswith("|"):
            cells = [c.strip() for c in trimmed.strip("|").split("|")]
            if all(re.match(r"^:?-+:?$", c) for c in cells if c):
                continue
            if not in_table:
                body_parts.append("<table border='1' cellspacing='0' cellpadding='6' style='border-collapse: collapse; width: 100%; margin: 12px 0;'>")
                in_table = True
            row_html = "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>"
            body_parts.append(row_html)
        else:
            if in_table:
                body_parts.append("</table>")
                in_table = False
            if trimmed:
                body_parts.append(f"<p style='margin: 4px 0; line-height: 1.6;'>{html.escape(line)}</p>")
            else:
                body_parts.append("<br/>")

    if in_table:
        body_parts.append("</table>")

    body_html = "\n".join(body_parts)

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>{escaped_title}</title>
  <style>
    body {{
      font-family: 'Malgun Gothic', '맑은 고딕', 'Apple SD Gothic Neo', sans-serif;
      line-height: 1.6;
      color: #222;
      padding: 24px;
      max-width: 800px;
      margin: 0 auto;
    }}
    table {{ border: 1px solid #ddd; border-collapse: collapse; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 12px; }}
    th {{ background-color: #f5f5f5; font-weight: bold; }}
  </style>
</head>
<body>
  <h2 style="border-bottom: 2px solid #3498DB; padding-bottom: 8px;">{escaped_title}</h2>
  {body_html}
</body>
</html>"""

    path.write_text(html_content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# HWPX (OWPML) export — minimal but spec-shaped package verified against a real
# Hancom Office .hwpx container layout.
# ---------------------------------------------------------------------------

_HWPX_NS = (
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
    'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
    'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
    'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" '
    'xmlns:opf="http://www.idpf.org/2007/opf/" '
    'xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
    'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/"'
)

_HWPX_HEADER_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<hh:head {_HWPX_NS} version="1.5" secCnt="1"><hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/><hh:refList><hh:fontfaces itemCnt="7">{{FONTFACES}}</hh:fontfaces><hh:borderFills itemCnt="2"><hh:borderFill id="1" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0"><hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/><hh:leftBorder type="NONE" width="0.1 mm" color="#000000"/><hh:rightBorder type="NONE" width="0.1 mm" color="#000000"/><hh:topBorder type="NONE" width="0.1 mm" color="#000000"/><hh:bottomBorder type="NONE" width="0.1 mm" color="#000000"/><hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/></hh:borderFill><hh:borderFill id="2" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0"><hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/><hh:leftBorder type="SOLID" width="0.4 mm" color="#000000"/><hh:rightBorder type="SOLID" width="0.4 mm" color="#000000"/><hh:topBorder type="SOLID" width="0.4 mm" color="#000000"/><hh:bottomBorder type="SOLID" width="0.4 mm" color="#000000"/><hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/></hh:borderFill></hh:borderFills><hh:charProperties itemCnt="1"><hh:charPr id="0" height="1000" textColor="#000000" shadeColor="none" useFontSpace="0" useKerning="0" symMark="NONE" borderFillIDRef="1"><hh:fontRef hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/><hh:ratio hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/><hh:spacing hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/><hh:relSz hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/><hh:offset hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/><hh:underline type="NONE" shape="SOLID" color="#000000"/><hh:strikeout shape="NONE" color="#000000"/><hh:outline type="NONE"/><hh:shadow type="NONE" color="#B2B2B2" offsetX="10" offsetY="10"/></hh:charPr></hh:charProperties><hh:tabProperties itemCnt="1"><hh:tabPr id="0" autoTabLeft="0" autoTabRight="0"/></hh:tabProperties><hh:numberings itemCnt="1"><hh:numbering id="1" start="0"><hh:paraHead start="1" level="1" align="LEFT" useInstWidth="1" autoIndent="1" widthAdjust="0" textOffsetType="PERCENT" textOffset="50" numFormat="DIGIT" charPrIDRef="4294967295" checkable="0">^1.</hh:paraHead><hh:paraHead start="1" level="2" align="LEFT" useInstWidth="1" autoIndent="1" widthAdjust="0" textOffsetType="PERCENT" textOffset="50" numFormat="HANGUL_SYLLABLE" charPrIDRef="4294967295" checkable="0">^2.</hh:paraHead><hh:paraHead start="1" level="3" align="LEFT" useInstWidth="1" autoIndent="1" widthAdjust="0" textOffsetType="PERCENT" textOffset="50" numFormat="DIGIT" charPrIDRef="4294967295" checkable="0">^3)</hh:paraHead><hh:paraHead start="1" level="4" align="LEFT" useInstWidth="1" autoIndent="1" widthAdjust="0" textOffsetType="PERCENT" textOffset="50" numFormat="HANGUL_SYLLABLE" charPrIDRef="4294967295" checkable="0">^4)</hh:paraHead><hh:paraHead start="1" level="5" align="LEFT" useInstWidth="1" autoIndent="1" widthAdjust="0" textOffsetType="PERCENT" textOffset="50" numFormat="DIGIT" charPrIDRef="4294967295" checkable="0">(^5)</hh:paraHead><hh:paraHead start="1" level="6" align="LEFT" useInstWidth="1" autoIndent="1" widthAdjust="0" textOffsetType="PERCENT" textOffset="50" numFormat="HANGUL_SYLLABLE" charPrIDRef="4294967295" checkable="0">(^6)</hh:paraHead><hh:paraHead start="1" level="7" align="LEFT" useInstWidth="1" autoIndent="1" widthAdjust="0" textOffsetType="PERCENT" textOffset="50" numFormat="CIRCLED_DIGIT" charPrIDRef="4294967295" checkable="1">^7</hh:paraHead></hh:numbering></hh:numberings><hh:paraProperties itemCnt="1"><hh:paraPr id="0" tabPrIDRef="0" condense="0" fontLineHeight="0" snapToGrid="1" suppressLineNumbers="0" checked="0"><hh:align horizontal="JUSTIFY" vertical="BASELINE"/><hh:heading type="NONE" idRef="0" level="0"/><hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="KEEP_WORD" widowOrphan="0" keepWithNext="0" keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/><hh:autoSpacing eAsianEng="0" eAsianNum="0"/><hh:margin><hc:intent value="0" unit="HWPUNIT"/><hc:left value="0" unit="HWPUNIT"/><hc:right value="0" unit="HWPUNIT"/><hc:prev value="0" unit="HWPUNIT"/><hc:next value="0" unit="HWPUNIT"/></hh:margin><hh:lineSpacing type="PERCENT" value="160" unit="HWPUNIT"/><hh:border borderFillIDRef="1" offsetLeft="0" offsetRight="0" offsetTop="0" offsetBottom="0" connect="0" ignoreMargin="0"/></hh:paraPr></hh:paraProperties><hh:styles itemCnt="1"><hh:style id="0" type="PARA" name="바탕글" engName="Normal" paraPrIDRef="0" charPrIDRef="0" nextStyleIDRef="0" langID="1042" lockForm="0"/></hh:styles></hh:refList><hh:compatibleDocument targetProgram="HWP201X"><hh:layoutCompatibility/></hh:compatibleDocument><hh:docOption><hh:linkinfo path="" pageInherit="1" footnoteInherit="0"/></hh:docOption><hh:trackchageConfig flags="56"/></hh:head>"""

_HWPX_FONTFACE = (
    '<hh:fontface lang="{lang}" fontCnt="1"><hh:font id="0" face="맑은 고딕" type="TTF" isEmbedded="0">'
    '<hh:typeInfo familyType="FCAT_GOTHIC" weight="5" proportion="3" contrast="2" strokeVariation="0" '
    'armStyle="0" letterform="2" midline="0" xHeight="4"/></hh:font></hh:fontface>'
)

_HWPX_SECPR = (
    '<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000" tabStopVal="4000" '
    'tabStopUnit="HWPUNIT" outlineShapeIDRef="1" memoShapeIDRef="0" textVerticalWidthHead="0" masterPageCnt="0">'
    '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
    '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
    '<hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" border="SHOW_ALL" '
    'fill="SHOW_ALL" hideFirstPageNum="0" hideFirstEmptyLine="0" showLineNumber="0"/>'
    '<hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>'
    '<hp:pagePr landscape="WIDELY" width="59528" height="84188" gutterType="LEFT_ONLY">'
    '<hp:margin header="2834" footer="2834" gutter="0" left="5669" right="5669" top="1417" bottom="1417"/>'
    '</hp:pagePr>'
    '<hp:footNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
    '<hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/>'
    '<hp:noteSpacing betweenNotes="283" belowLine="567" aboveLine="850"/>'
    '<hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="EACH_COLUMN" beneathText="0"/></hp:footNotePr>'
    '<hp:endNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
    '<hp:noteLine length="14692344" type="SOLID" width="0.12 mm" color="#000000"/>'
    '<hp:noteSpacing betweenNotes="0" belowLine="567" aboveLine="850"/>'
    '<hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="END_OF_DOCUMENT" beneathText="0"/></hp:endNotePr>'
    '<hp:pageBorderFill type="BOTH" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" '
    'fillArea="PAPER"><hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
    '<hp:pageBorderFill type="EVEN" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" '
    'fillArea="PAPER"><hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
    '<hp:pageBorderFill type="ODD" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" '
    'fillArea="PAPER"><hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
    '<hp:masterPage idRef=""/></hp:secPr>'
)

_HWPX_LINESEG = ('<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" '
                 'baseline="850" spacing="600" horzpos="0" horzsize="46884" flags="393216"/></hp:linesegarray>')


def _hwpx_para(pid: int, inner_run: str) -> str:
    return (f'<hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">{inner_run}</hp:run>{_HWPX_LINESEG}</hp:p>')


def _hwpx_table(rows: list[list[str]], next_pid: int) -> tuple[str, int]:
    ncols = max((len(r) for r in rows), default=1)
    cell_w = 48188 // max(1, ncols)
    tr_xml = []
    for r_i, row in enumerate(rows):
        tc_xml = []
        for c_i in range(ncols):
            cell = html.escape(row[c_i] if c_i < len(row) else "")
            tc_xml.append(
                f'<hp:tc name="" header="{1 if r_i == 0 else 0}" hasMargin="0" protect="0" editable="0" '
                f'dirty="0" borderFillIDRef="2"><hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
                f'vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
                f'hasTextRef="0" hasNumRef="0">'
                f'{_hwpx_para(next_pid, f"<hp:t>{cell}</hp:t>")}'
                f'</hp:subList><hp:cellAddr colAddr="{c_i}" rowAddr="{r_i}"/>'
                f'<hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="{cell_w}" height="800"/>'
                f'<hp:cellMargin left="510" right="510" top="141" bottom="141"/></hp:tc>')
            next_pid += 1
        tr_xml.append("<hp:tr>" + "".join(tc_xml) + "</hp:tr>")
    tbl = (f'<hp:tbl pageBreak="CELL" repeatHeader="1" rowCnt="{len(rows)}" colCnt="{ncols}" cellSpacing="0" '
           f'borderFillIDRef="2" noAdjust="0">'
           f'<hp:sz width="48188" widthRelTo="FILL" height="800" heightRelTo="PARA" protect="0"/>'
           f'<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
           f'vertRelTo="PARA" vertAlign="TOP" horzRelTo="PARA" horzAlign="LEFT"/>'
           f'<hp:outMargin left="283" right="283" top="283" bottom="283"/>'
           f'<hp:inMargin left="510" right="510" top="141" bottom="141"/>'
           + "".join(tr_xml) + "</hp:tbl>")
    return tbl, next_pid


def _is_md_table_row(line: str) -> bool:
    t = line.strip()
    return t.startswith("|") and t.endswith("|") and t.count("|") >= 2


def _is_md_separator_row(cells: list[str]) -> bool:
    return all(re.match(r"^:?-+:?$", c) for c in cells if c) and any(c for c in cells)


def export_to_hwpx(content: str, filepath: str | Path, title: str = "션 업무 산출물") -> Path:
    """Export deliverable content to a real .hwpx package Hancom Word can open.

    Plain lines become paragraphs; `|a|b|` markdown tables become real tables.
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    blocks: list[tuple[str, object]] = []  # ("p", line) | ("tbl", rows)
    for line in content.splitlines():
        if _is_md_table_row(line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if _is_md_separator_row(cells):
                continue
            if blocks and blocks[-1][0] == "tbl":
                blocks[-1][1].append(cells)
            else:
                blocks.append(("tbl", [cells]))
        else:
            blocks.append(("p", line))

    paras: list[str] = []
    pid = 0
    for kind, payload in blocks:
        if kind == "tbl":
            tbl_xml, pid = _hwpx_table(payload, pid + 1)  # type: ignore[arg-type]
            paras.append(_hwpx_para(pid, tbl_xml + "<hp:t></hp:t>"))
            pid += 1
        else:
            text = html.escape(str(payload))
            paras.append(_hwpx_para(pid, f"<hp:t>{text}</hp:t>"))
            pid += 1

    if paras:
        first = paras[0]
        # Inject section properties into the first paragraph's run.
        paras[0] = first.replace('<hp:run charPrIDRef="0">', '<hp:run charPrIDRef="0">' + _HWPX_SECPR, 1)

    section_xml = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<hs:sec {_HWPX_NS}>{"".join(paras)}</hs:sec>'
    )

    fontfaces = "".join(
        _HWPX_FONTFACE.format(lang=lang)
        for lang in ("HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER")
    )
    header_xml = _HWPX_HEADER_XML.replace("{FONTFACES}", fontfaces)

    title_esc = html.escape(title)
    content_hpf = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<opf:package {_HWPX_NS} version="1.2" unique-identifier="bookid" id="">'
        f'<opf:metadata><opf:title>{title_esc}</opf:title><opf:language>ko</opf:language>'
        f'<opf:meta name="creator" content="text">SyeonPet</opf:meta></opf:metadata>'
        f'<opf:manifest>'
        f'<opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
        f'<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
        f'<opf:item id="settings" href="settings.xml" media-type="application/xml"/>'
        f'</opf:manifest><opf:spine>'
        f'<opf:itemref idref="header" linear="yes"/><opf:itemref idref="section0" linear="yes"/>'
        f'</opf:spine></opf:package>'
    )

    container_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container" '
        'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf"><ocf:rootfiles>'
        '<ocf:rootfile full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/>'
        '<ocf:rootfile full-path="Preview/PrvText.txt" media-type="text/plain"/>'
        '<ocf:rootfile full-path="META-INF/container.rdf" media-type="application/rdf+xml"/>'
        '</ocf:rootfiles></ocf:container>'
    )

    container_rdf = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="">'
        '<ns0:hasPart xmlns:ns0="http://www.hancom.co.kr/hwpml/2016/meta/pkg#" '
        'rdf:resource="Contents/section0.xml"/></rdf:Description>'
        '<rdf:Description rdf:about="Contents/section0.xml">'
        '<ns1:hasRDF xmlns:ns1="http://www.hancom.co.kr/hwpml/2016/meta/pkg#" '
        'rdf:resource="Contents/section0.rdf"/></rdf:Description></rdf:RDF>'
    )

    settings_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<ha:HWPApplicationSetting {_HWPX_NS}>'
        '<ha:CaretPosition listIDRef="0" paraIDRef="0" pos="0"/>'
        '<config:config-item-set name="PrintInfo">'
        '<config:config-item name="PrintAutoFootNote" type="boolean">false</config:config-item>'
        '<config:config-item name="PrintAutoHeadNote" type="boolean">false</config:config-item>'
        '<config:config-item name="PrintMethod" type="short">4</config:config-item>'
        '<config:config-item name="OverlapSize" type="short">0</config:config-item>'
        '<config:config-item name="PrintCropMark" type="short">0</config:config-item>'
        '<config:config-item name="BinderHoleType" type="short">0</config:config-item>'
        '<config:config-item name="ZoomX" type="short">100</config:config-item>'
        '<config:config-item name="ZoomY" type="short">100</config:config-item>'
        '</config:config-item-set></ha:HWPApplicationSetting>'
    )

    version_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" '
        'tagetApplication="WORDPROCESSOR" major="5" minor="1" micro="1" buildNumber="0" os="1" '
        'xmlVersion="1.5" application="SyeonPet" appVersion=""/>'
    )

    manifest_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>'
    )

    preview_text = "\n".join(line for line in content.splitlines()).strip() or title

    with zipfile.ZipFile(path, "w") as zf:
        # mimetype must be the first entry and stored uncompressed.
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in (
            ("version.xml", version_xml),
            ("settings.xml", settings_xml),
            ("Contents/content.hpf", content_hpf),
            ("Contents/header.xml", header_xml),
            ("Contents/section0.xml", section_xml),
            ("META-INF/container.xml", container_xml),
            ("META-INF/container.rdf", container_rdf),
            ("META-INF/manifest.xml", manifest_xml),
            ("Preview/PrvText.txt", preview_text),
        ):
            zf.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return path


def export_deliverable(
    content: str,
    filepath: str | Path,
    title: str = "션 업무 산출물",
) -> Path:
    """Export deliverable content dispatched by file extension."""
    path = Path(filepath)
    ext = path.suffix.lower()
    if ext == ".hwpx":
        return export_to_hwpx(content, path, title=title)
    if ext == ".md":
        return export_to_markdown(content, path)
    if ext in (".html", ".htm"):
        return export_to_html(content, path, title=title)
    if ext == ".csv":
        return export_to_csv(content, path)
    return export_to_txt(content, path)
