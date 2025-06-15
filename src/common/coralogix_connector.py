import json
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

class CoralogixConnector:
    """
    Connector class for querying logs from Coralogix API.
    Based on: https://coralogix.com/docs/developer-portal/apis/data-query/direct-archive-query-http-api/
    """

    def __init__(self, api_key: str, domain: str = "gloat.coralogix.com"):
        """
        Initialize the Coralogix connector.

        Args:
            api_key: Coralogix API key
            domain: Coralogix API domain (default: api.coralogix.com)
        """
        self.api_key = api_key
        self.base_url = f"https://ng-api-http.coralogix.com/api/v1/dataprime/query"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    def query_logs(
        self,
        start_time: datetime,
        end_time: datetime,
        message_text: Optional[str] = None,
        filters: Dict[str, str] = None,
        search_archive=False,
        limit=100,
    ) -> Dict[str, Any]:
        # Build query components
        query_parts = []

        # Add message text filter if provided
        if message_text:
            query_parts.append(f"message:\"{message_text}\"")

        # Add additional filters
        if filters:
            for key, value in filters.items():
                # if isinstance(value, str):
                #     value = '"' + value + '"'
                query_parts.append(f"{key}:{value}")

        # Join all query parts with AND
        query_string = " AND ".join(query_parts) if query_parts else "*"

        # Prepare request payload
        metadata = {
            "syntax": "QUERY_SYNTAX_LUCENE",
            "tier": "TIER_ARCHIVE" if search_archive else "TIER_FREQUENT_SEARCH",
            "limit": limit,
        }
        if start_time:
            metadata["startDate"] = start_time.isoformat()
        if end_time:
            metadata["endDate"] = end_time.isoformat()
        payload = {
            "query": query_string,
            "metadata": metadata,
        }

        logger.debug(f"Sending query to Coralogix: {json.dumps(payload)}")

        try:
            response = requests.post(self.base_url, headers=self.headers, json=payload)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Error querying Coralogix API: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}, content: {e.response.text}")
            raise

        resp = [json.loads(l) for l in response.text.splitlines() if l]
        if 'queryId' in resp[0]:
            # this is just metadata, skip it
            resp = resp[1:]
        if not resp:
            errmsg = f"Empty response from Coralogix API: {response.text}"
            if not search_archive:
                errmsg += " Consider using search_archive=True."
            return [{"error": errmsg}]
        if len(resp) != 1:
            raise ValueError(f"Expected 1 result, got {len(resp)}")
        try:
            return [
                r['userData']
                for r in resp[0]['result']['results']
            ]
        except KeyError as e:
            logger.exception(f"Error parsing response: {resp}")
            return resp


if __name__ == '__main__':
    from config import Settings
    settings = Settings()
    coralogix = CoralogixConnector(api_key=settings.coralogix_api_key, domain=settings.coralogix_domain)
    start_time = datetime.now() - timedelta(days=1)
    end_time = datetime.now()
    ret = coralogix.query_logs(
        start_time=start_time,
        end_time=end_time,
        message_text="index",
        filters={"coralogix.metadata.applicationName": "ontology"},
        search_archive=False,
        limit=10,
    )
    for resp in ret:
        print(resp)

