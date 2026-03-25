terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Uncomment to use remote state in S3
  # backend "s3" {
  #   bucket         = "your-terraform-state-bucket"
  #   key            = "musicstore/terraform.tfstate"
  #   region         = var.aws_region
  #   dynamodb_table = "terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "musicstore"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ----------------------------------------------------------
# Data sources
# ----------------------------------------------------------
data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}
