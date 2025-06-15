import enum


class OurRegions(str, enum.Enum):
    eu_central_1 = "euc1"
    us_east_1 = "use1"
    ap_southeast_1 = "apse1"
    ap_southeast_2 = "apse2"


class OurAccounts(str, enum.Enum):
    prod = "prod"
    stg = "stg"
    dev = "dev"

VERBOSE_ACCOUNT_NAME = {
    OurAccounts.prod: "production",
    OurAccounts.stg: "staging",
    OurAccounts.dev: "development",
}
VERBOSE_REGION_NAME = {
    OurRegions.eu_central_1: "eu-central-1",
    OurRegions.us_east_1: "us-east-1",
    OurRegions.ap_southeast_1: "ap-southeast-1",
    OurRegions.ap_southeast_2: "ap-southeast-2",
}
