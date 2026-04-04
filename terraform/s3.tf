# ----------------------------------------------------------
# S3 bucket for album files (replaces local volume mount)
# ----------------------------------------------------------
locals {
  albums_bucket = var.albums_bucket_name != "" ? var.albums_bucket_name : "${var.app_name}-albums-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "albums" {
  bucket        = local.albums_bucket
  force_destroy = false
  tags          = { Name = "${var.app_name}-albums" }
}

resource "aws_s3_bucket_versioning" "albums" {
  bucket = aws_s3_bucket.albums.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "albums" {
  bucket = aws_s3_bucket.albums.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "albums" {
  bucket                  = aws_s3_bucket.albums.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
