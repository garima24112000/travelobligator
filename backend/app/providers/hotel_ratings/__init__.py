from app.providers.hotel_ratings.base import HotelRatingsProvider
from app.providers.hotel_ratings.factory import get_hotel_ratings_provider
from app.providers.hotel_ratings.not_connected import NotConnectedHotelRatingsProvider
from app.providers.hotel_ratings.scraped_adapter import ScrapedLocalHotelRatingsProvider

__all__ = [
    "HotelRatingsProvider",
    "NotConnectedHotelRatingsProvider",
    "ScrapedLocalHotelRatingsProvider",
    "get_hotel_ratings_provider",
]
