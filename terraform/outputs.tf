output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = aws_lb.main.dns_name
}

output "alb_zone_id" {
  description = "Route 53 hosted zone ID for the ALB (use for ALIAS records)"
  value       = aws_lb.main.zone_id
}

output "ecr_apache_url" {
  description = "ECR repository URL for the Apache/web.py image"
  value       = aws_ecr_repository.apache.repository_url
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint"
  value       = aws_db_instance.postgres.address
  sensitive   = true
}

output "albums_bucket" {
  description = "S3 bucket name for album files"
  value       = aws_s3_bucket.albums.bucket
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "push_commands" {
  description = "Docker push commands to build and push the Apache image to ECR"
  value = <<-EOT
    # 1. Authenticate Docker to ECR
    aws ecr get-login-password --region ${var.aws_region} | \
      docker login --username AWS --password-stdin ${aws_ecr_repository.apache.repository_url}

    # 2. Build the image
    docker build -t ${aws_ecr_repository.apache.repository_url}:latest .

    # 3. Push to ECR
    docker push ${aws_ecr_repository.apache.repository_url}:latest
  EOT
}
