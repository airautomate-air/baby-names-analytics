"""Run every country fetcher in sequence and print a summary."""
import fetch_us
import fetch_fr
import fetch_uk
import fetch_au
from _common import stats


COUNTRY_NAMES = {
    'US': 'United States (SSA)',
    'FR': 'France (INSEE)',
    'GB': 'United Kingdom (ONS, E&W)',
    'AU': 'Australia (NSW BDM + VIC BDM)',
}


def main():
    fetch_us.main()
    fetch_fr.main()
    fetch_uk.main()
    fetch_au.main()

    print()
    print("=" * 78)
    print(f"{'COUNTRY':36} {'YEARS':12} {'ROWS':>10} {'UNIQUE':>10}  TOP NAMES")
    print("=" * 78)
    for cc in ('US', 'FR', 'GB', 'AU'):
        s = stats(cc)
        label = COUNTRY_NAMES[cc]
        top = ', '.join((s.get('top_girls') or [])[:2] + (s.get('top_boys') or [])[:2])
        print(f"{label:36} {s.get('years',''):12} {s.get('rows',0):>10,} "
              f"{s.get('unique_names',0):>10,}  {top}")


if __name__ == '__main__':
    main()
