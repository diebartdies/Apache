# ----------------------------------------------------------
# ECR repositories
# ----------------------------------------------------------
resource "aws_ecr_repository" "apache" {
  name                 = "${var.app_name}/apache"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "${var.app_name}-apache-ecr" }
}

# Lifecycle policy: keep only last 10 images
resource "aws_ecr_lifecycle_policy" "apache" {
  repository = aws_ecr_repository.apache.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}
