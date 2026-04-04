variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "sa-east-1"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "app_name" {
  description = "Application name"
  type        = string
  default     = "musicstore"
}

# ----------------------------------------------------------
# Networking
# ----------------------------------------------------------
variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDRs for public subnets (one per AZ)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDRs for private subnets (one per AZ)"
  type        = list(string)
  default     = ["10.0.10.0/24", "10.0.11.0/24"]
}

# ----------------------------------------------------------
# RDS (PostgreSQL)
# ----------------------------------------------------------
variable "db_name" {
  description = "PostgreSQL database name"
  type        = string
  default     = "musicstore"
}

variable "db_username" {
  description = "PostgreSQL master username"
  type        = string
  default     = "musicstore"
  sensitive   = true
}

variable "db_password" {
  description = "PostgreSQL master password (use AWS Secrets Manager in production)"
  type        = string
  sensitive   = true
  default     = "" # Set via TF_VAR_db_password or terraform.tfvars
}

variable "db_instance_class" {
  description = "RDS instance type"
  type        = string
  default     = "db.t3.micro"
}

# ----------------------------------------------------------
# ECS / Fargate
# ----------------------------------------------------------
variable "apache_cpu" {
  description = "CPU units for the Apache/web.py Fargate task"
  type        = number
  default     = 512
}

variable "apache_memory" {
  description = "Memory (MB) for the Apache/web.py Fargate task"
  type        = number
  default     = 1024
}

variable "apache_desired_count" {
  description = "Number of Apache Fargate task replicas"
  type        = number
  default     = 1
}

# ----------------------------------------------------------
# ACM certificate (HTTPS)
# ----------------------------------------------------------
variable "acm_certificate_arn" {
  description = "ARN of an existing ACM certificate for HTTPS on the ALB (leave empty to skip HTTPS listener)"
  type        = string
  default     = ""
}

# ----------------------------------------------------------
# Albums bucket
# ----------------------------------------------------------
variable "albums_bucket_name" {
  description = "S3 bucket name for album files (must be globally unique)"
  type        = string
  default     = "" # defaults to "<app_name>-albums-<account_id>"
}

# ----------------------------------------------------------
# EasyDNS (optional)
# ----------------------------------------------------------
variable "easydns_hostname" {
  type    = string
  default = ""
}

variable "easydns_username" {
  type      = string
  default   = ""
  sensitive = true
}

variable "easydns_password" {
  type      = string
  default   = ""
  sensitive = true
}
