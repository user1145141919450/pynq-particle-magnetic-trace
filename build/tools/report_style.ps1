param(
    [string]$Html,
    [string]$Pdf,
    [string]$Docx,
    [int]$Toc = 1,
    [string]$Header = "PYNQ Particle Magnetic Trace System - Design Report",
    [int]$HeadingLeft = 0
)

# Word COM formatting for the competition report.
# ASCII-only on purpose: the script must run on a Windows PowerShell 5.1
# without relying on the file encoding for Chinese literals.
#   - A4, margins 2.5/2.5/3.0/2.5 cm
#   - body: SimSun 12pt, Times New Roman for Latin, 1.5 line spacing
#   - headings: SimHei, 16/14/12pt
#   - cover: no page number
#   - section 2 (abstract + TOC): Roman page numbers
#   - section 3 (body): Arabic page numbers, restart at 1

$ErrorActionPreference = 'Stop'
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0

try {
    $doc = $word.Documents.Open($Html, $false, $true)

    # Embed linked pictures (Word imports <img> as a link by default); a
    # linked DOCX breaks as soon as it is moved or sent to another machine.
    foreach ($s in $doc.InlineShapes) {
        try {
            if ($s.Type -eq 4) {   # wdInlineShapeLinkedPicture
                $s.LinkFormat.SavePictureWithDocument = $true
                $s.LinkFormat.BreakLink()
            }
        } catch {}
    }

    # Word's HTML import ignores CSS max-width and keeps the natural size of
    # the bitmap (e.g. a 1280px screenshot becomes 33.9cm wide). Scale any
    # oversized picture to the text width, keeping its aspect ratio and the
    # original image data, so the PDF stays sharp without overflowing.
    $maxImgW = $word.CentimetersToPoints(15.4)
    foreach ($s in $doc.InlineShapes) {
        try {
            $oldW = $s.Width
            $oldH = $s.Height
            if ($oldW -gt $maxImgW -and $oldW -gt 0) {
                $s.Width = $maxImgW
                $s.Height = $oldH * ($maxImgW / $oldW)
            }
        } catch {}
    }

    # ---------- page setup ----------
    $doc.PageSetup.PageWidth = $word.CentimetersToPoints(21.0)
    $doc.PageSetup.PageHeight = $word.CentimetersToPoints(29.7)
    $doc.PageSetup.TopMargin = $word.CentimetersToPoints(2.5)
    $doc.PageSetup.BottomMargin = $word.CentimetersToPoints(2.0)
    $doc.PageSetup.LeftMargin = $word.CentimetersToPoints(3.0)
    $doc.PageSetup.RightMargin = $word.CentimetersToPoints(2.5)

    # ---------- paragraph styles ----------
    $h1 = New-Object System.Collections.ArrayList
    $inCover = $true
    $inAbstract = $false
    $prevCode = $null
    foreach ($p in $doc.Paragraphs) {
        $t = $p.Range.Text.Trim()
        if ($t -eq '[[SEC_SUMMARY]]') {
            $inCover = $false; $inAbstract = $true
            $p.Range.Font.Color = 16777215; $p.Range.Font.Size = 1
            continue
        }
        if ($t -eq '[[SEC_BODY]]') {
            $inAbstract = $false
            $p.Range.Font.Color = 16777215; $p.Range.Font.Size = 1
            continue
        }
        if ($t.Length -eq 0) { continue }
        if ($p.Range.Information(12)) { continue }   # inside a table

        # Documents without the section markers (e.g. the collaboration
        # record) still need body formatting after the cover. The first real
        # H2 is distinguishable from the 16pt cover subtitle by its outline
        # level (the subtitle is a plain div, not a heading).
        if ($inCover -and $t.Length -gt 0 -and
            $p.Range.Font.Size -eq 16 -and $p.OutlineLevel -le 3) {
            $inCover = $false
        }

        if ($inCover) {
            # cover: keep the sizes from HTML, set fonts only
            $p.Range.Font.Name = "Times New Roman"
            if ($p.Range.Font.Size -ge 16) {
                $p.Range.Font.NameFarEast = "SimHei"
            } else {
                $p.Range.Font.NameFarEast = "SimSun"
            }
            continue
        }

        if ($t -eq '[[TOC]]' -or $t -eq '[[SEC_SUMMARY]]' -or $t -eq '[[SEC_BODY]]') { continue }

        if ($p.Range.Font.Name -match 'Consolas' -or $p.Style.NameLocal -match 'HTML') {
            if ($prevCode -ne $null) {
                $prevCode.Range.ParagraphFormat.KeepWithNext = $true
            }
            $p.Range.Font.Size = 9
            $p.Range.ParagraphFormat.LineSpacingRule = 0
            $p.Range.ParagraphFormat.Alignment = 0
            $p.Range.ParagraphFormat.LeftIndent = 0
            $p.Range.ParagraphFormat.RightIndent = 0
            $p.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 0
            $p.Range.ParagraphFormat.SpaceBefore = 4
            $p.Range.ParagraphFormat.SpaceAfter = 4
            $p.Range.ParagraphFormat.KeepTogether = $true
            $prevCode = $p
            continue
        }
        $prevCode = $null

        # Figures and captions: always centered, never inherit the body's
        # 2-character first-line indent (that indent shifts centered images
        # to the right even when Alignment=1).
        $isFigure = ($p.Range.InlineShapes.Count -gt 0)
        $figMark = [string][char]0x56FE + ' '
        $tabMark = [string][char]0x8868 + ' '
        $isCaption = ($t.StartsWith($figMark) -or $t.StartsWith($tabMark))
        if ($isFigure -or $isCaption) {
            $p.Range.ParagraphFormat.Alignment = 1
            $p.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 0
            $p.Range.ParagraphFormat.LeftIndent = 0
            $p.Range.ParagraphFormat.RightIndent = 0
            $p.Range.ParagraphFormat.LineSpacingRule = 0
            if ($isFigure) {
                $p.Range.ParagraphFormat.SpaceBefore = 6
                $p.Range.ParagraphFormat.SpaceAfter = 2
                $p.Range.ParagraphFormat.KeepWithNext = $true
            } else {
                $p.Range.ParagraphFormat.SpaceBefore = 2
                $p.Range.ParagraphFormat.SpaceAfter = 6
            }
            $p.Range.Font.Name = "Times New Roman"
            $p.Range.Font.NameFarEast = "SimSun"
            continue
        }

        # Keywords line: flush left (no 2-character first-line indent), with
        # a little space above and below.
        $kwCn = [string][char]0x5173 + [string][char]0x952E + [string][char]0x8BCD
        if ($t.StartsWith($kwCn) -or $t.StartsWith('Keywords:')) {
            $p.Range.Font.Name = "Times New Roman"
            $p.Range.Font.NameFarEast = "SimSun"
            $p.Range.Font.Size = 12
            $p.Range.ParagraphFormat.LineSpacingRule = 1
            $p.Range.ParagraphFormat.Alignment = 0
            $p.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 0
            $p.Range.ParagraphFormat.SpaceBefore = 6
            $p.Range.ParagraphFormat.SpaceAfter = 6
            continue
        }

        $size = $p.Range.Font.Size
        if ($size -eq 16) {
            # level 1 heading
            $p.Style = $doc.Styles.Item(-2)
            $p.Range.Font.Name = "Times New Roman"
            $p.Range.Font.NameFarEast = "SimHei"
            $p.Range.Font.Size = 16
            $p.Range.Font.Bold = $false
            $p.Range.Font.Color = 0
            $p.Range.ParagraphFormat.LineSpacingRule = 0
            $p.Range.ParagraphFormat.SpaceBefore = 12
            $p.Range.ParagraphFormat.SpaceAfter = 6
            if ($HeadingLeft -eq 1) {
                $p.Range.ParagraphFormat.Alignment = 0
            } else {
                $p.Range.ParagraphFormat.Alignment = 1
            }
            $p.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 0
            $p.Range.ParagraphFormat.KeepWithNext = $true
            [void]$h1.Add($p)
        } elseif ($size -eq 14) {
            # level 2 heading
            $p.Style = $doc.Styles.Item(-3)
            $p.Range.Font.Name = "Times New Roman"
            $p.Range.Font.NameFarEast = "SimHei"
            $p.Range.Font.Size = 14
            $p.Range.Font.Bold = $false
            $p.Range.Font.Color = 0
            $p.Range.ParagraphFormat.LineSpacingRule = 0
            $p.Range.ParagraphFormat.SpaceBefore = 10
            $p.Range.ParagraphFormat.SpaceAfter = 4
            $p.Range.ParagraphFormat.Alignment = 0
            $p.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 0
            $p.Range.ParagraphFormat.KeepWithNext = $true
        } elseif ($t -match '^\d+\.\d+\.\d+') {
            # level 3 heading
            $p.Style = $doc.Styles.Item(-4)
            $p.Range.Font.Name = "Times New Roman"
            $p.Range.Font.NameFarEast = "SimHei"
            $p.Range.Font.Size = 12
            $p.Range.Font.Bold = $false
            $p.Range.Font.Color = 0
            $p.Range.ParagraphFormat.LineSpacingRule = 0
            $p.Range.ParagraphFormat.SpaceBefore = 8
            $p.Range.ParagraphFormat.SpaceAfter = 3
            $p.Range.ParagraphFormat.Alignment = 0
            $p.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 0
            $p.Range.ParagraphFormat.KeepWithNext = $true
        } else {
            # body: left-aligned, not justified. Justification stretches the
            # spaces between CJK and Latin words into visibly wide gaps.
            $p.Range.Font.Name = "Times New Roman"
            $p.Range.Font.NameFarEast = "SimSun"
            $p.Range.Font.Size = 12
            $p.Range.ParagraphFormat.LineSpacingRule = 1   # 1.5 lines
            $p.Range.ParagraphFormat.SpaceBefore = 0
            $p.Range.ParagraphFormat.SpaceAfter = 0
            if ($p.Range.ListFormat.ListType -ne 0) {
                # Numbered/bulleted items already have Word's list indent;
                # a 2-character first-line indent pushes the number out to
                # the right and leaves an ugly gap after it.
                $p.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 0
                $p.Range.ParagraphFormat.FirstLineIndent = 0
                # Start the number at the same 2-character indent as body
                # text, instead of Word's default deeper list indent.
                $p.Range.ParagraphFormat.LeftIndent = 24
            } else {
                $p.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 2
            }
            $p.Range.ParagraphFormat.Alignment = 0
            if ($inAbstract) { $p.Range.ParagraphFormat.SpaceAfter = 6 }
            # Short lead-in labels ("...命令：") stay with the code block below.
            if ($t.Length -le 20 -and $t.EndsWith([string][char]0xFF1A)) {
                $p.Range.ParagraphFormat.KeepWithNext = $true
            }
        }
    }

    # ---------- display equations ----------
    # Markers in the Markdown become real Word equations, so a formula like
    # theta=qB*dt/m is a stacked fraction instead of an inline a/b string.
    # Build the math symbols from code points so this script stays ASCII-only
    # for Windows PowerShell 5.1 (UTF-8 literals would be read as GBK).
    $theta = [char]0x03B8   # theta
    $Delta = [char]0x0394   # Delta
    $times = [char]0x00D7   # multiplication sign
    $arrow = [char]0x2190   # left arrow
    $eqDefs = @{
        '[[EQ_LORENTZ]]' = ('F=q(v' + $times + 'B)')
        '[[EQ_THETA]]'   = ($theta + '=qB' + $Delta + 't/m')
        '[[EQ_POS]]'     = ('p' + $arrow + 'p+v' + $Delta + 't')
    }
    foreach ($marker in $eqDefs.Keys) {
        $linear = $eqDefs[$marker]
        $found = $null
        foreach ($p in $doc.Paragraphs) {
            if ($p.Range.Text.Trim() -eq $marker) { $found = $p; break }
        }
        if ($found -ne $null) {
            $found.Range.ParagraphFormat.Alignment = 1
            $found.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 0
            $found.Range.ParagraphFormat.LeftIndent = 0
            $found.Range.ParagraphFormat.RightIndent = 0
            $found.Range.ParagraphFormat.LineSpacingRule = 0
            $found.Range.ParagraphFormat.SpaceBefore = 4
            $found.Range.ParagraphFormat.SpaceAfter = 4
            # Keep the paragraph mark: replacing the whole paragraph range
            # would merge the following prose into the equation.
            $r = $doc.Range($found.Range.Start, $found.Range.End - 1)
            $r.Text = $linear
            $r.Font.Name = "Cambria Math"
            $r.Font.Size = 12
            [void]$doc.OMaths.Add($r)
        }
    }
    # Inline equation: the reciprocal table sits in the middle of a sentence,
    # so replace the marker in place instead of using a display paragraph.
    $lceil = [char]0x2308
    $rceil = [char]0x2309
    $inlineDefs = @{
        '[[EQ_RECIP]]' = ([string]$lceil + '2^26/x' + [string]$rceil)
    }
    foreach ($marker in $inlineDefs.Keys) {
        $linear = $inlineDefs[$marker]
        foreach ($p in $doc.Paragraphs) {
            $t = $p.Range.Text
            $pos = $t.IndexOf($marker)
            if ($pos -ge 0) {
                $r = $doc.Range(($p.Range.Start + $pos), ($p.Range.Start + $pos + $marker.Length))
                $r.Text = $linear
                $r.Font.Name = "Cambria Math"
                $r.Font.Size = 12
                [void]$doc.OMaths.Add($r)
                break
            }
        }
    }

    if ($doc.OMaths.Count -gt 0) { $doc.OMaths.BuildUp() }

    # ---------- cover blank underlines ----------
    # Word's HTML import drops <u> on &nbsp; runs; apply the underline here.
    $colon = [char]0xFF1A
    foreach ($p in $doc.Paragraphs) {
        if ($p.Range.Information(3) -ne 1) { continue }
        $t = $p.Range.Text
        $pos = $t.IndexOf($colon)
        if ($pos -ge 0 -and $t.Trim().Length -le 10) {
            $start = $p.Range.Start + $pos + 1
            $end = $p.Range.End - 1
            if ($end -gt $start) {
                $doc.Range($start, $end).Font.Underline = 1
            }
        }
    }

    # ---------- tables ----------
    foreach ($t in $doc.Tables) {
        $t.Borders.Enable = $true
        try { $t.Rows.Alignment = 1 } catch {}   # center the table horizontally
        $t.Range.ParagraphFormat.Alignment = 1    # center cell text
        $t.Rows(1).HeadingFormat = $true
        $t.Rows(1).Range.Font.Bold = $true
        $t.Rows(1).Shading.BackgroundPatternColor = 16777215   # white: no tint in eye-care mode
        # Word does not draw the table's outer top border on a repeated header
        # row at the top of a continuation page; add an explicit top border to
        # the header cells so every page of a split table is closed.
        foreach ($cell in $t.Rows(1).Cells) {
            $b = $cell.Borders.Item(-1)
            $b.LineStyle = 1
            $b.LineWidth = 8   # 1.0pt: the page-top clip halves it visually
            $b.Color = -16777216
        }
        $t.Rows.AllowBreakAcrossPages = $false
        $t.Range.Font.Name = "Times New Roman"
        $t.Range.Font.NameFarEast = "SimSun"
        $t.Range.Font.Size = 9.5
        $t.Range.ParagraphFormat.LineSpacingRule = 0
        $t.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 0
        # HTML import puts 6pt before/after on every cell paragraph; that
        # padding is what pushes a whole row to the next page and leaves a
        # gap. Tighten it so tables paginate row-by-row instead of orphaning
        # the last row.
        $t.Range.ParagraphFormat.SpaceBefore = 0
        $t.Range.ParagraphFormat.SpaceAfter = 0
        $t.TopPadding = 0
        $t.BottomPadding = 0
        $t.LeftPadding = 3
        $t.RightPadding = 3
    }

    # ---------- sections / page numbers / TOC ----------
    if ($Toc -eq 1) {
        # Keep only the TOC heading ("目录") out of the generated TOC; do not
        # touch chapter 1. The string is built from char codes to keep this
        # script ASCII-only for Windows PowerShell 5.1.
        $tocText = [string]([char]0x76EE) + [string]([char]0x5F55)
        foreach ($hp in $h1) {
            if ($hp.Range.Text.Trim() -eq $tocText) {
                $hp.Style = $doc.Styles.Item(-1)   # Normal
                $hp.Range.Font.Name = "Times New Roman"
                $hp.Range.Font.NameFarEast = "SimHei"
                $hp.Range.Font.Size = 16
                $hp.Range.Font.Bold = $false
                $hp.Range.ParagraphFormat.Alignment = 1
                $hp.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 0
            }
        }

        # Insert section breaks at the ASCII markers.
        foreach ($mk in @('[[SEC_SUMMARY]]', '[[SEC_BODY]]')) {
            $mp = $null
            foreach ($p in $doc.Paragraphs) {
                if ($p.Range.Text.Trim() -eq $mk) { $mp = $p; break }
            }
            if ($mp -ne $null) {
                [void]$doc.Sections.Add($mp.Range)
                $mp.Range.Text = ""
                $mp.Range.Font.Size = 1
                $mp.Range.ParagraphFormat.LineSpacingRule = 4
                $mp.Range.ParagraphFormat.LineSpacing = 1
                $mp.Range.ParagraphFormat.SpaceBefore = 0
                $mp.Range.ParagraphFormat.SpaceAfter = 0
                $mp.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 0
            }
        }

        # Replace [[TOC]] with a Word TOC field.
        $pToc = $null
        foreach ($p in $doc.Paragraphs) {
            if ($p.Range.Text.Trim() -eq '[[TOC]]') { $pToc = $p; break }
        }
        if ($pToc -ne $null) {
            $r = $pToc.Range
            $r.Text = ""
            [void]$doc.TablesOfContents.Add($r, $true, 1, 3)
        }

        if ($doc.Sections.Count -lt 3) { throw ("SECTION_COUNT=" + $doc.Sections.Count) }

        # Page numbering per section.
        $sec1 = $doc.Sections.Item(1)
        $sec1.Headers(1).Range.Text = ""
        $sec1.Footers(1).Range.Text = ""

        $sec2 = $doc.Sections.Item(2)
        $sec2.Headers(1).LinkToPrevious = $false
        $sec2.Headers(1).Range.Text = $Header
        $sec2.Headers(1).Range.Font.Name = "Times New Roman"
        $sec2.Headers(1).Range.Font.Size = 9
        $sec2.Headers(1).Range.Font.Color = 8421504
        $sec2.Headers(1).Range.ParagraphFormat.Alignment = 1
        $sec2.Footers(1).LinkToPrevious = $false
        $sec2.Footers(1).Range.Text = ""
        $sec2.Footers(1).Range.ParagraphFormat.Alignment = 1
        $sec2.Footers(1).PageNumbers.RestartNumberingAtSection = $true
        $sec2.Footers(1).PageNumbers.StartingNumber = 1
        $sec2.Footers(1).PageNumbers.NumberStyle = 1   # uppercase Roman
        [void]$sec2.Footers(1).PageNumbers.Add(2, $true)

        $sec3 = $doc.Sections.Item(3)
        $sec3.Headers(1).LinkToPrevious = $false
        $sec3.Headers(1).Range.Text = $Header
        $sec3.Headers(1).Range.Font.Name = "Times New Roman"
        $sec3.Headers(1).Range.Font.Size = 9
        $sec3.Headers(1).Range.Font.Color = 8421504
        $sec3.Headers(1).Range.ParagraphFormat.Alignment = 1
        $sec3.Footers(1).LinkToPrevious = $false
        $sec3.Footers(1).Range.Text = ""
        $sec3.Footers(1).Range.ParagraphFormat.Alignment = 1
        $sec3.Footers(1).PageNumbers.RestartNumberingAtSection = $true
        $sec3.Footers(1).PageNumbers.StartingNumber = 1
        $sec3.Footers(1).PageNumbers.NumberStyle = 0   # Arabic
        [void]$sec3.Footers(1).PageNumbers.Add(2, $true)

        # Chapters flow continuously; only the TOC keeps an explicit page
        # break. Forcing every H1 onto a new page left large blank areas.
        foreach ($p in $doc.Paragraphs) {
            if ($p.OutlineLevel -eq 1) {
                $p.Range.ParagraphFormat.PageBreakBefore = $false
            }
        }

        if ($doc.TablesOfContents.Count -gt 0) {
            $doc.TablesOfContents(1).Update()
        }
        $doc.Fields.Update()
    } else {
        # Single section: Arabic page numbers, no TOC.
        $sec = $doc.Sections.Item(1)
        $sec.Headers(1).Range.Text = $Header
        $sec.Headers(1).Range.Font.Name = "Times New Roman"
        $sec.Headers(1).Range.Font.Size = 9
        $sec.Headers(1).Range.Font.Color = 8421504
        $sec.Headers(1).Range.ParagraphFormat.Alignment = 1
        $sec.Footers(1).Range.Text = ""
        $sec.Footers(1).Range.ParagraphFormat.Alignment = 1
        [void]$sec.Footers(1).PageNumbers.Add(2, $true)
    }

    # ---------- restore marker-adjacent headings + TOC page break ----------
    if ($Toc -eq 1) {
        $summary = [string]([char]0x6458) + [string]([char]0x8981)
        $tocText2 = [string]([char]0x76EE) + [string]([char]0x5F55)
        foreach ($p in $doc.Paragraphs) {
            $t = $p.Range.Text.Trim()
            if ($t -eq $summary -or $t -eq 'Abstract' -or $t -match '^1\s') {
                $p.Style = $doc.Styles.Item(-2)   # Heading 1
                $p.Range.Font.Name = "Times New Roman"
                $p.Range.Font.NameFarEast = "SimHei"
                $p.Range.Font.Size = 16
                $p.Range.Font.Bold = $false
                $p.Range.Font.Color = 0
                $p.Range.ParagraphFormat.LineSpacingRule = 0
                $p.Range.ParagraphFormat.SpaceBefore = 12
                $p.Range.ParagraphFormat.SpaceAfter = 6
                $p.Range.ParagraphFormat.Alignment = 1
                $p.Range.ParagraphFormat.CharacterUnitFirstLineIndent = 0
            }
            if ($t -eq $tocText2) {
                $p.Range.ParagraphFormat.PageBreakBefore = $true
            }
        }
        if ($doc.TablesOfContents.Count -gt 0) {
            $doc.TablesOfContents.Item(1).Update()
        }
    }

    # ---------- document properties (anonymize team info) ----------
    try { $word.UserName = "PYNQ Particle Magnetic Trace System" } catch {}
    $props = @(
        @("Title", "PYNQ Particle Magnetic Trace System"),
        @("Author", "PYNQ Particle Magnetic Trace System"),
        @("Last Author", ""),
        @("Company", ""),
        @("Manager", ""),
        @("Subject", ""),
        @("Keywords", "")
    )
    foreach ($pair in $props) {
        try { $doc.BuiltInDocumentProperties.Item($pair[0]).Value = $pair[1] } catch {}
    }

    # ---------- save ----------
    if ($Docx -ne "") { $doc.SaveAs2($Docx, 16) }
    $doc.ExportAsFixedFormat($Pdf, 17)
    $doc.Close($false)
} finally {
    $word.Quit()
}
