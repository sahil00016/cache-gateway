# Remote state in S3 with native locking (Terraform >= 1.10, no DynamoDB table).
#
# With ephemeral infrastructure, losing state is worse than usual: `destroy` can
# no longer find what it created, so resources keep billing silently. The bucket
# is versioned, so a corrupted state is restorable rather than orphaning a
# running instance.
#
# The bucket is created once, by hand, because a bootstrap Terraform config to
# create the backend its sibling config depends on is a second thing to
# understand for no gain:
#
#   aws s3 mb s3://cache-gateway-tfstate-280155396532 --region ap-south-1
#   aws s3api put-bucket-versioning \
#     --bucket cache-gateway-tfstate-280155396532 \
#     --versioning-configuration Status=Enabled
#   aws s3api put-public-access-block \
#     --bucket cache-gateway-tfstate-280155396532 \
#     --public-access-block-configuration \
#       BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

terraform {
  required_version = ">= 1.10"

  backend "s3" {
    bucket       = "cache-gateway-tfstate-280155396532"
    key          = "cache-gateway/prod/terraform.tfstate"
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true
  }
}
