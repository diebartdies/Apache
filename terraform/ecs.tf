# ----------------------------------------------------------
# CloudWatch log group
# ----------------------------------------------------------
resource "aws_cloudwatch_log_group" "apache" {
  name              = "/ecs/${var.app_name}/apache"
  retention_in_days = 30
}

# ----------------------------------------------------------
# ECS Cluster
# ----------------------------------------------------------
resource "aws_ecs_cluster" "main" {
  name = "${var.app_name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "${var.app_name}-cluster" }
}

# ----------------------------------------------------------
# ECS Task Definition — Apache + web.py
# ----------------------------------------------------------
resource "aws_ecs_task_definition" "apache" {
  family                   = "${var.app_name}-apache"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.apache_cpu
  memory                   = var.apache_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "apache"
      image     = "${aws_ecr_repository.apache.repository_url}:latest"
      essential = true

      portMappings = [
        { containerPort = 80, protocol = "tcp" }
      ]

      environment = [
        { name = "ALBUMS_DIR", value = "/opt/albums" },
        { name = "POSTGRES_HOST", value = aws_db_instance.postgres.address },
        { name = "POSTGRES_PORT", value = tostring(aws_db_instance.postgres.port) },
        { name = "POSTGRES_DB", value = var.db_name },
        { name = "POSTGRES_USER", value = var.db_username },
        { name = "POSTGRES_PASSWORD", value = var.db_password },
        { name = "SYNC_DISCS_ON_STARTUP", value = "1" },
        { name = "EASYDNS_HOSTNAME", value = var.easydns_hostname },
        { name = "EASYDNS_USERNAME", value = var.easydns_username },
        { name = "EASYDNS_PASSWORD", value = var.easydns_password }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.apache.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = { Name = "${var.app_name}-apache-task" }
}

# ----------------------------------------------------------
# ECS Service
# ----------------------------------------------------------
resource "aws_ecs_service" "apache" {
  name            = "${var.app_name}-apache"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.apache.arn
  desired_count   = var.apache_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.apache.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.apache.arn
    container_name   = "apache"
    container_port   = 80
  }

  # Allow rolling deploys without downtime
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  depends_on = [
    aws_lb_listener.http,
    aws_iam_role_policy_attachment.ecs_execution
  ]

  lifecycle {
    ignore_changes = [task_definition] # allow CI/CD to update the task
  }

  tags = { Name = "${var.app_name}-apache-service" }
}
