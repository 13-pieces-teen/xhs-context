param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

# Export PNG sizes from the approved artwork; no changes to its design or colors.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$brandPath = Join-Path $ProjectRoot 'assets\brand'
$iconsPath = Join-Path $ProjectRoot 'browser-extension\icons'
New-Item -ItemType Directory -Path $iconsPath -Force | Out-Null
$sourceImage = [System.Drawing.Image]::FromFile((Join-Path $brandPath 'xhs-context-mark.png'))
try {
    foreach ($size in @(16, 20, 24, 32, 48, 64, 128, 256, 512)) {
        $bitmap = [System.Drawing.Bitmap]::new($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $attributes = [System.Drawing.Imaging.ImageAttributes]::new()
        try {
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
            $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $attributes.SetWrapMode([System.Drawing.Drawing2D.WrapMode]::TileFlipXY)
            $graphics.DrawImage($sourceImage, [System.Drawing.Rectangle]::new(0, 0, $size, $size), 0, 0,
                $sourceImage.Width, $sourceImage.Height, [System.Drawing.GraphicsUnit]::Pixel, $attributes)
            $destination = if ($size -le 128) { Join-Path $iconsPath "icon-$size.png" }
                else { Join-Path $brandPath "xhs-context-mark-$size.png" }
            $bitmap.Save($destination, [System.Drawing.Imaging.ImageFormat]::Png)
            Write-Output $destination
        }
        finally {
            $attributes.Dispose()
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    }
}
finally { $sourceImage.Dispose() }
