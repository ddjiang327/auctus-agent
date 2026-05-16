#!/usr/bin/env python3
"""
Create simple placeholder icons for Auctus Agent.
Generates icon.ico (Windows) and icon.icns (macOS) from a simple design.
"""
from PIL import Image, ImageDraw, ImageFont
import os

def create_icon(size=256):
    """Create a simple icon image."""
    # Create image with transparent background
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Draw a blue circle background
    margin = size // 8
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(66, 133, 244, 255),  # Google Blue
        outline=(25, 103, 210, 255),
        width=size // 32
    )
    
    # Draw "A" letter in white
    try:
        # Try to use a system font
        font_size = size // 2
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except:
        font = ImageFont.load_default()
    
    # Get text bounding box
    text = "A"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    # Center the text
    x = (size - text_width) // 2
    y = (size - text_height) // 2 - size // 16
    
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)
    
    return img

def create_windows_icon():
    """Create Windows .ico file with multiple sizes."""
    sizes = [16, 32, 48, 64, 128, 256]
    images = []
    
    for size in sizes:
        img = create_icon(size)
        images.append(img)
    
    # Save as .ico
    output_path = os.path.join(os.path.dirname(__file__), 'icon.ico')
    images[0].save(
        output_path,
        format='ICO',
        sizes=[(s, s) for s in sizes],
        append_images=images[1:]
    )
    print(f"Created: {output_path}")
    return output_path

def create_macos_icon():
    """Create macOS .icns file (saved as PNG, needs conversion on macOS)."""
    # Create 1024x1024 for macOS
    img = create_icon(1024)
    
    # Save as PNG (on macOS, use iconutil to convert to .icns)
    output_path = os.path.join(os.path.dirname(__file__), 'icon.png')
    img.save(output_path, 'PNG')
    print(f"Created: {output_path}")
    print("Note: On macOS, convert to .icns using:")
    print("  mkdir icon.iconset")
    print("  for size in 16 32 64 128 256 512 1024; do")
    print("    sips -z $size $size icon.png --out icon.iconset/icon_${size}x${size}.png")
    print("  done")
    print("  iconutil -c icns icon.iconset")
    return output_path

if __name__ == '__main__':
    print("Creating Auctus Agent icons...")
    create_windows_icon()
    create_macos_icon()
    print("\nDone!")
