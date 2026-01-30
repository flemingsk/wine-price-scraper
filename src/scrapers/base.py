class BaseScraper:
    def scrape(self, url, product):
        """
        Must return:
        (price_amount, currency, raw_price_text, availability)
        """
        raise NotImplementedError
