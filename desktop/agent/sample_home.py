"""Seed the disposable session's home folder with ordinary-looking files so the
assistant has something real to find, open, read and organise in a demo."""
import os
from pathlib import Path

HOME = Path(os.environ.get('HOME', '/tmp'))

FILES = {
    'Documents/Quarterly Report Q3.md': '''# Quarterly Report — Q3

## Summary
Revenue grew 18% quarter over quarter, driven by the new subscription plan.
Customer churn fell from 4.1% to 3.2%. Support response time improved to 3h.

## Highlights
- 1,240 new paying customers (target was 1,000)
- Mobile app rating rose from 4.2 to 4.6
- Launched in two new markets: Turkey and Portugal

## Risks
- Server costs rose 22%, faster than revenue
- Two senior engineers leave in Q4; hiring is behind plan

## Next quarter
Focus on infrastructure cost, onboarding speed and the partner programme.
''',
    'Documents/Meeting notes - product sync.txt': '''Product sync — Tuesday

Attendees: Lina, Omar, Sam, Defne

- Onboarding flow: Omar shows new 3-step version. Everyone prefers it. Ship next sprint.
- Pricing page: Lina wants an annual plan toggle. Needs design by Friday.
- Bug: export to PDF loses images on long documents. Sam investigating.
- Decision: postpone dark mode until after the partner launch.

Action items
1. Omar — finish onboarding copy (Thursday)
2. Lina — annual plan design (Friday)
3. Sam — PDF export bug root cause (Wednesday)
4. Defne — book user interviews for next week
''',
    'Documents/todo.txt': '''- reply to Defne about interviews
- renew domain before the 15th
- prepare demo for the hackathon
- buy coffee filters
''',
    'Documents/Recipes/lentil soup.txt': '''Red lentil soup (4 servings)
1 cup red lentils, 1 onion, 1 carrot, 2 tbsp tomato paste, 1 tsp cumin, 1 tsp paprika,
6 cups water or stock, lemon, salt.
Sauté onion and carrot, add paste and spices, add lentils and water, simmer 25 min, blend, finish with lemon.
''',
    'Documents/budget.csv': '''month,rent,groceries,transport,fun
July,900,310,85,140
August,900,295,92,210
September,900,330,78,120
''',
    'Downloads/invoice-0423.txt': 'Invoice 0423\nDesign services — October\nTotal due: 1,450 EUR\nDue date: 20 November\n',
    'Downloads/setup-notes.txt': 'Wi-Fi password is on the router sticker.\nPrinter: Brother HL-L2350.\n',
}


def pictures():
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    folder = HOME / 'Pictures'
    folder.mkdir(parents=True, exist_ok=True)
    for name, colors in {'sunset.png': ((250, 140, 60), (120, 40, 110)), 'ocean.png': ((40, 120, 200), (10, 40, 90)),
                         'forest.png': ((70, 150, 80), (20, 60, 30))}.items():
        target = folder / name
        if target.exists(): continue
        image = Image.new('RGB', (960, 640))
        draw = ImageDraw.Draw(image)
        for y in range(640):
            t = y / 639
            draw.line((0, y, 960, y), fill=tuple(round(a + (b - a) * t) for a, b in zip(*colors)))
        image.save(target)


def populate():
    try:
        for relative, content in FILES.items():
            target = HOME / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        for folder in ('Desktop', 'Music', 'Videos'):
            (HOME / folder).mkdir(exist_ok=True)
        pictures()
    except OSError:
        pass
