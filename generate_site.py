#!/usr/bin/env python3
"""
Generate static site for baby names analytics.
"""
import os
import csv
from collections import defaultdict
from pathlib import Path

# Configuration
DATA_DIR = Path('.')  # where the yob*.txt files are
OUTPUT_DIR = Path('docs')  # GitHub Pages will serve from /docs (or we can change to root)
YEARS = range(1880, 2026)  # we have data up to 2025

# We'll create the output directory
OUTPUT_DIR.mkdir(exist_ok=True)
(OUTPUT_DIR / 'name').mkdir(exist_ok=True)
(OUTPUT_DIR / 'year').mkdir(exist_ok=True)
(OUTPUT_DIR / 'compare').mkdir(exist_ok=True)

# Data structures
# name_data[name][year] = count
name_data = defaultdict(lambda: defaultdict(int))
# year_data[year] = list of (name, count, sex) sorted by count descending
year_data = defaultdict(list)

print("Reading data...")
for year in YEARS:
    filename = DATA_DIR / f'yob{year}.txt'
    if not filename.exists():
        print(f"Warning: {filename} not found")
        continue
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            name, sex, count = line.split(',')
            count = int(count)
            name_data[name][year] = count
            year_data[year].append((name, count, sex))
    # Sort the year data by count descending (and then by name for tie-breaking)
    year_data[year].sort(key=lambda x: (-x[1], x[0]))
    print(f"  Processed {year}: {len(year_data[year])} entries")

# Compute total counts per name to get top names
total_counts = defaultdict(int)
for name, years in name_data.items():
    total_counts[name] = sum(years.values())

# Get top 1000 names by total count
top_names = sorted(total_counts.items(), key=lambda x: x[1], reverse=True)[:1000]
top_names_set = set(name for name, _ in top_names)

print(f"Total unique names: len(name_data)")
print(f"Top {len(top_names)} names selected for page generation.")

# Generate homepage
def generate_homepage():
    html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Baby Names Analytics</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
            margin: 0;
            padding: 2rem;
            background-color: #f5f5f5;
            color: #333;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        h1 {
            color: #2c3e50;
            text-align: center;
        }
        .search-box {
            margin: 2rem 0;
            text-align: center;
        }
        .search-box input {
            padding: 0.75rem;
            width: 70%;
            max-width: 400px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 1rem;
        }
        .trending {
            margin-top: 3rem;
        }
        .trending h2 {
            color: #3498db;
            border-bottom: 2px solid #ecf0f1;
            padding-bottom: 0.5rem;
        }
        .trending-list {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 1rem;
            list-style: none;
            padding: 0;
        }
        .trending-list li {
            background: white;
            padding: 1rem;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-align: center;
        }
        .trending-list h3 {
            margin-top: 0;
            color: #2c3e50;
        }
        .trending-list p {
            color: #7f8c8d;
            margin: 0.5rem 0 0;
        }
        .footer {
            text-align: center;
            margin-top: 3rem;
            color: #7f8c8d;
            font-size: 0.9rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Baby Names Analytics</h1>
        <p>Explore the popularity and trends of baby names over time.</p>
        
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="Enter a name to explore...">
            <p id="searchHint">Try popular names like Olivia, Liam, Emma, Noah</p>
        </div>
        
        <div class="trending">
            <h2>Top Names of All Time (by total usage)</h2>
            <ul class="trending-list">
'''
    # Add top 20 names as trending
    for rank, (name, total) in enumerate(top_names[:20], start=1):
        html += f'''                <li>
                    <h3>{name}</h3>
                    <p>{total:,} total babies</p>
                </li>\n'''
    html += '''            </ul>
        </div>
        
        <div class="footer">
            <p>Data source: U.S. Social Security Administration</p>
            <p>&copy; 2026 Baby Names Analytics</p>
        </div>
    </div>
    <script>
        // Simple redirect to name page if entered
        document.getElementById('searchInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                const name = this.value.trim();
                if (name) {
                    // Format name for URL: lowercase, replace spaces with hyphens
                    const urlName = name.toLowerCase().replace(/[^a-z0-9]+/g, '');
                    window.location.href = `/name/${urlName}.html`;
                }
            }
        });
    </script>
</body>
</html>'''
    with open(OUTPUT_DIR / 'index.html', 'w') as f:
        f.write(html)

# Generate name pages
def generate_name_page(name):
    # Get the data for this name across years
    years_data = name_data[name]
    # Prepare data for chart: years and counts
    years = sorted(years_data.keys())
    counts = [years_data[y] for y in years]
    
    # Determine gender: if the name appears mostly as one gender, we can show that
    # We'll compute the total by sex across all years
    sex_totals = {'F': 0, 'M': 0}
    for year in years_data:
        # We need to know the sex for each year; we didn't store it separately.
        # Let's adjust: we'll store sex data as well.
        # For simplicity, we'll assume the sex is consistent (most names are predominantly one sex).
        # We'll compute this by re-reading or storing separately.
        # We'll change the data structure to also track sex.
        pass
    # Since we didn't store sex, we'll skip gender info for now and add later.
    
    # For now, we'll generate a simple page showing the trend as a table.
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name} - Baby Name Analytics</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
            margin: 0;
            padding: 2rem;
            background-color: #f5f5f5;
            color: #333;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
        }}
        h1 {{
            color: #2c3e50;
        }}
        .nav {{
            margin-bottom: 2rem;
        }}
        .nav a {{
            color: #3498db;
            text-decoration: none;
        }}
        .nav a:hover {{
            text-decoration: underline;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}
        .stat {{
            background: white;
            padding: 1.5rem;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-align: center;
        }}
        .stat-value {{
            font-size: 2rem;
            font-weight: bold;
            color: #2c3e50;
        }}
        .stat-label {{
            color: #7f8c8d;
            margin-top: 0.5rem;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 2rem;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        th, td {{
            padding: 1rem;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background-color: #ecf0f1;
            font-weight: 600;
        }}
        tr:hover {{
            background-color: #f8f9fa;
        }}
        .year-column {{
            width: 10%;
            font-family: monospace;
        }}
        .count-column {{
            width: 15%;
            text-align: right;
        }}
        .rank-column {{
            width: 15%;
            text-align: right;
        }}
        .footer {{
            text-align: center;
            margin-top: 3rem;
            color: #7f8c8d;
            font-size: 0.9rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <nav class="nav">
            <a href="/">← Back to all names</a>
        </nav>
        <h1>{name}</h1>
        
        <div class="stats">
            <div class="stat">
                <div class="stat-value">{sum(years_data.values()):,}</div>
                <div class="stat-label">Total babies</div>
            </div>
            <div class="stat">
                <div class="stat-value">{len(years_data)}</div>
                <div class="stat-label">Years appearing</div>
            </div>
            <div class="stat">
                <div class="stat-value">{max(years_data.values()) if years_data else 0:,}</div>
                <div class="stat-label">Peak popularity</div>
            </div>
        </div>
        
        <h2>Popularity Over Time</h2>
        <table>
            <thead>
                <tr>
                    <th class="year-column">Year</th>
                    <th class="count-column">Babies</th>
                    <th class="rank-column">Rank</th>
                </tr>
            </thead>
            <tbody>
'''
    # Add rows for each year where the name appeared
    for year in years:
        count = years_data[year]
        # Find rank for this year
        # We have year_data[year] which is a list of (name, count, sex) sorted by count
        # We'll search for the name in that list to get rank.
        # This is O(n) per year, but we can precompute a rank dict for each year.
        # For simplicity, we'll do a linear search since we're only doing top 1000 names.
        rank = None
        for i, (n, c, s) in enumerate(year_data[year]):
            if n == name:
                rank = i + 1
                break
        if rank is None:
            rank = '>1000'  # not in top 1000 for that year
        html += f'''                <tr>
                    <td class="year-column">{year}</td>
                    <td class="count-column">{count:,}</td>
                    <td class="rank-column">{rank}</td>
                </tr>\n'''
    html += '''            </tbody>
        </table>
        
        <div class="footer">
            <p>Data source: U.S. Social Security Administration</p>
            <p>&copy; 2026 Baby Names Analytics</p>
        </div>
    </div>
</body>
</html>'''
    # Write to file
    # Create a safe filename: lowercase, replace non-alphanumeric with hyphens
    safe_name = ''.join(c if c.isalnum() else '-' for c in name.lower()).strip('-')
    # Avoid empty name
    if not safe_name:
        safe_name = 'name'
    with open(OUTPUT_DIR / 'name' / f'{safe_name}.html', 'w') as f:
        f.write(html)

# Generate year pages (top names for each year)
def generate_year_page(year):
    # Get top 100 names for this year (or all if less)
    top_for_year = year_data[year][:100]  # already sorted by count descending
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Top Baby Names of {year} - Baby Name Analytics</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
            margin: 0;
            padding: 2rem;
            background-color: #f5f5f5;
            color: #333;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
        }}
        h1 {{
            color: #2c3e50;
        }}
        .nav {{
            margin-bottom: 2rem;
        }}
        .nav a {{
            color: #3498db;
            text-decoration: none;
        }}
        .nav a:hover {{
            text-decoration: underline;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 2rem;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        th, td {{
            padding: 1rem;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background-color: #ecf0f1;
            font-weight: 600;
        }}
        tr:hover {{
            background-color: #f8f9fa;
        }}
        .rank-column {{
            width: 10%;
            text-align: center;
            font-weight: bold;
            color: #e74c3c;
        }}
        .name-column {{
            width: 30%;
        }}
        .count-column {{
            width: 20%;
            text-align: right;
        }}
        .sex-column {{
            width: 10%;
            text-align: center;
        }}
        .footer {{
            text-align: center;
            margin-top: 3rem;
            color: #7f8c8d;
            font-size: 0.9rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <nav class="nav">
            <a href="/">← Back to all names</a>
            <a href="/year/{year-1}/">← {year-1}</a> |
            <a href="/year/{year+1}/">{year+1} →</a>
        </nav>
        <h1>Top Baby Names of {year}</h1>
        
        <table>
            <thead>
                <tr>
                    <th class="rank-column">Rank</th>
                    <th class="name-column">Name</th>
                    <th class="sex-column">Sex</th>
                    <th class="count-column">Number of Babies</th>
                </tr>
            </thead>
            <tbody>
'''
    for rank, (name, count, sex) in enumerate(top_for_year, start=1):
        html += f'''                <tr>
                    <td class="rank-column">{rank}</td>
                    <td class="name-column"><a href="/name/{''.join(c if c.isalnum() else '_' for c in name.lower()).strip('_')}.html">{name}</a></td>
                    <td class="sex-column">{sex}</td>
                    <td class="count-column">{count:,}</td>
                </tr>\n'''
    html += '''            </tbody>
        </table>
        
        <div class="footer">
            <p>Data source: U.S. Social Security Administration</p>
            <p>&copy; 2026 Baby Names Analytics</p>
        </div>
    </div>
</body>
</html>'''
    with open(OUTPUT_DIR / 'year' / f'{year}.html', 'w') as f:
        f.write(html)

# Generate comparison page (optional, for now we'll skip or do a simple version)
# We'll generate a few sample comparisons for popular names.

def generate_comparison_page(name1, name2):
    # We'll show a side-by-side trend chart (as a table)
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name1} vs {name2} - Baby Name Analytics</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
            margin: 0;
            padding: 2rem;
            background-color: #f5f5f5;
            color: #333;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
        }}
        h1 {{
            color: #2c3e50;
            text-align: center;
        }}
        .nav {{
            margin-bottom: 2rem;
        }}
        .nav a {{
            color: #3498db;
            text-decoration: none;
        }}
        .nav a:hover {{
            text-decoration: underline;
        }}
        .comparison {{
            display: flex;
            gap: 2rem;
            margin-bottom: 2rem;
        }}
        .comparison-column {{
            flex: 1;
            background: white;
            padding: 1.5rem;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .comparison-column h2 {{
            margin-top: 0;
            color: #3498db;
            border-bottom: 1px solid #ecf0f1;
            padding-bottom: 0.5rem;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            padding: 0.75rem;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background-color: #ecf0f1;
            font-weight: 600;
        }}
        tr:hover {{
            background-color: #f8f9fa;
        }}
        .year-column {{
            width: 20%;
            font-family: monospace;
        }}
        .count-column {{
            width: 30%;
            text-align: right;
        }}
        .footer {{
            text-align: center;
            margin-top: 3rem;
            color: #7f8c8d;
            font-size: 0.9rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <nav class="nav">
            <a href="/">← Back to all names</a>
        </nav>
        <h1>{name1} vs {name2}</h1>
        
        <div class="comparison">
            <div class="comparison-column">
                <h2>{name1}</h2>
                <table>
                    <thead>
                        <tr>
                            <th class="year-column">Year</th>
                            <th class="count-column">Babies</th>
                        </tr>
                    </thead>
                    <tbody>
'''
    # Add data for name1
    years1 = sorted(name_data[name1].keys())
    for year in years1:
        count = name_data[name1][year]
        html += f'''                        <tr>
                            <td class="year-column">{year}</td>
                            <td class="count-column">{count:,}</td>
                        </tr>\n'''
    html += '''                    </tbody>
                </table>
            </div>
            <div class="comparison-column">
                <h2>{name2}</h2>
                <table>
                    <thead>
                        <tr>
                            <th class="year-column">Year</th>
                            <th class="count-column">Babies</th>
                        </tr>
                    </thead>
                    <tbody>
'''
    # Add data for name2
    years2 = sorted(name_data[name2].keys())
    for year in years2:
        count = name_data[name2][year]
        html += f'''                        <tr>
                            <td class="year-column">{year}</td>
                            <td class="count-column">{count:,}</td>
                        </tr>\n'''
    html += '''                    </tbody>
                </table>
            </div>
        </div>
        
        <div class="footer">
            <p>Data source: U.S. Social Security Administration</p>
            <p>&copy; 2026 Baby Names Analytics</p>
        </div>
    </div>
</body>
</html>'''
    safe_name1 = ''.join(c if c.isalnum() else '-' for c in name1.lower()).strip('-')
    safe_name2 = ''.join(c if c.isalnum() else '-' for c in name2.lower()).strip('-')
    with open(OUTPUT_DIR / 'compare' / f'{safe_name1}-vs-{safe_name2}.html', 'w') as f:
        f.write(html)

def main():
    print("Generating homepage...")
    generate_homepage()
    
    print(f"Generating name pages for top {len(top_names)} names...")
    for i, (name, _) in enumerate(top_names):
        if i % 100 == 0:
            print(f"  Processed {i} names...")
        generate_name_page(name)
    
    print("Generating year pages...")
    for year in YEARS:
        generate_year_page(year)
    
    print("Generating a few comparison pages...")
    # Generate comparisons for the top 5 names
    top5 = [name for name, _ in top_names[:5]]
    for i in range(len(top5)):
        for j in range(i+1, len(top5)):
            generate_comparison_page(top5[i], top5[j])
    
    print("Done!")

if __name__ == '__main__':
    main()