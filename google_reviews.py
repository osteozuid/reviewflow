import os
import requests
from dotenv import load_dotenv

load_dotenv()


def _fetch_place_details(fields='reviews'):
    api_key  = os.getenv('GOOGLE_PLACES_API_KEY', '')
    place_id = os.getenv('GOOGLE_PLACE_ID', '')

    if not api_key:
        raise ValueError(
            "GOOGLE_PLACES_API_KEY ontbreekt in .env of Instellingen"
        )
    if not place_id:
        raise ValueError(
            "GOOGLE_PLACE_ID ontbreekt in .env of Instellingen"
        )

    url = 'https://maps.googleapis.com/maps/api/place/details/json'
    params = {
        'place_id': place_id,
        'fields': fields,
        'language': 'nl',
        'key': api_key,
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    status = data.get('status')
    if status != 'OK':
        raise ValueError(f"Google Places API fout: {status} — {data.get('error_message', '')}")

    return data.get('result', {})


def fetch_reviews():
    """
    Haalt volledige review-objecten op via de Google Places API.
    Geeft lijst van dicts terug met: author_name, rating, text,
    relative_time_description, time, profile_photo_url.
    """
    result = _fetch_place_details(fields='reviews,rating,user_ratings_total')
    return result.get('reviews', [])


def fetch_reviewer_names():
    """Haalt alleen de reviewer-namen op (voor de blocked-list)."""
    reviews = fetch_reviews()
    return [r['author_name'] for r in reviews if r.get('author_name')]


def fetch_place_summary():
    """Haalt het totaal aantal reviews en de gemiddelde score op."""
    result = _fetch_place_details(fields='rating,user_ratings_total')
    return {
        'rating': result.get('rating'),
        'total':  result.get('user_ratings_total'),
    }
