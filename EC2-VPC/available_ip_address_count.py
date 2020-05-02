from boto3.session import Session
from botocore.exceptions import ClientError
import click
from configparser import ConfigParser
import logging
from os.path import expanduser, join

# Update the root logger to get messages at DEBUG and above
logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger("botocore").setLevel(logging.CRITICAL)
logging.getLogger("boto3").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)

aws_profiles = []
try:
    cp = ConfigParser()
    cp.read(join(expanduser("~"), ".aws", "credentials"))
    aws_profiles = cp.sections()
except Exception as e:
    logging.error(e)


def process_region(client, operation_params, threshold):
    cnt = 0
    paginator = client.get_paginator("describe_subnets")
    for page in paginator.paginate(**operation_params):
        for item in page["Subnets"]:

            mask = int(item["CidrBlock"].split("/")[1])
            # 5 addresses reserved per subnet: .0 network, .1 VPC router, .2 DNS, .3 future, .255 broadcast
            usable_ip_cnt = 2**(32-mask) - 5
            available_ip_cnt = item["AvailableIpAddressCount"]

            data = [
                f"{item['SubnetId']}: {item['CidrBlock']}",
                f"usable={usable_ip_cnt}",
                f"used={usable_ip_cnt-available_ip_cnt}",
                f"available={available_ip_cnt}",
                "***" if usable_ip_cnt < threshold else "",
            ]
            print(", ".join(data))

            cnt += 1
    return cnt


def process_account(session, profile, account_id, aws_region, subnet_id, threshold):
    operation_params = {"SubnetIds": [subnet_id]} if subnet_id else {}

    regions = session.get_available_regions("ec2") if aws_region == "all" else [aws_region]
    for region in regions:
        logging.debug(f"Checking {account_id} {profile} {region}")
        try:
            client = session.client("ec2", region_name=region)
            cnt = process_region(client, operation_params, threshold)
            if cnt and subnet_id:
                return cnt

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code in ["AuthFailure", "UnrecognizedClientException"]:
                logging.warning(f"Unable to process region {region}: {error_code}")
            else:
                raise
        except Exception as e:
            import traceback
            traceback.print_exc()


################################################################################
# Entry point

@click.command()
@click.option("--profile", "-p", help="AWS profile name")
@click.option("--subnetid", "-s", help="ID of the subnet. Default: Describe all subnets.", default=None)
@click.option("--threshold", "-t", help="Highlight subnet count if number of available IP address is less than this threshold. Default: 8.", default=8)
@click.option("--region", "-r", help="AWS Region; use 'all' for all regions", default="ap-southeast-2")
def main(profile, subnetid, threshold, region):
    accounts_processed = []
    profile_names = [profile] if profile else aws_profiles
    
    for profile_name in profile_names:
        try:
            session = Session(profile_name=profile_name)
            account_id = session.client("sts").get_caller_identity()["Account"]
            if account_id in accounts_processed:
                continue
            accounts_processed.append(account_id)
            
            if process_account(session, profile_name, account_id, region, subnetid, threshold) is not None:
                break

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code in ["ExpiredToken", "AccessDenied"]:
                logging.warning(f"{profile_name} {error_code}. Skipped")
            else:
                raise


if __name__ == "__main__": main()