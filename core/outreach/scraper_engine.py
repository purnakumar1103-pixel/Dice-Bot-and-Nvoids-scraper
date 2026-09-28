class BaseScraper:
    def scrape(self):
        """
        Must return a list of dictionaries.
        Expected keys: Job Title, Company, Recruiter Name, Recruiter Email, Phone, Description, URL
        """
        raise NotImplementedError

class DiceAdapterScraper(BaseScraper):
    """
    Adapter that takes the existing Dice auto apply bot's raw job queue 
    and bridges it to the outreach format.
    """
    def __init__(self, dice_jobs):
        self.dice_jobs = dice_jobs
        
    def scrape(self):
        outreach_jobs = []
        for d_job in self.dice_jobs:
            # Note: Dice jobs usually don't expose recruiter name/email directly on the search page,
            # so this adapter will rely on the JD text parsed by the bot.
            outreach_jobs.append({
                "Job Title": d_job.get("title", "Unknown Role"),
                "Company": d_job.get("company", "Unknown Company"),
                "Recruiter Name": "",  
                "Recruiter Email": "", 
                "Phone": "",
                "Description": d_job.get("description", ""),
                "URL": d_job.get("job_link", "")
            })
        return outreach_jobs

class LinkedInScraper(BaseScraper):
    """
    Stub for future LinkedIn scraping expansion.
    """
    def __init__(self, queries, limit=10, pipeline=None, skip_email=False, log_callback=None):
        self.queries = queries
        self.limit = limit
        self.pipeline = pipeline
        self.skip_email = skip_email
        self.log = log_callback or print

    def scrape(self):
        self.log("[LinkedInScraper] ❌ LinkedIn scraper is not fully implemented yet.")
        return []

class IndeedScraper(BaseScraper):
    """
    Stub for future Indeed scraping expansion.
    """
    def __init__(self, queries, limit=10, pipeline=None, skip_email=False, log_callback=None):
        self.queries = queries
        self.limit = limit
        self.pipeline = pipeline
        self.skip_email = skip_email
        self.log = log_callback or print

    def scrape(self):
        self.log("[IndeedScraper] ❌ Indeed scraper is not fully implemented yet.")
        return []
