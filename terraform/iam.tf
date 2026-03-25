# ----------------------------------------------------------
# IAM role for ECS task execution (pull from ECR, write logs)
# ----------------------------------------------------------
data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${var.app_name}-ecs-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ----------------------------------------------------------
# IAM role for the ECS task itself (S3 albums bucket access)
# ----------------------------------------------------------
resource "aws_iam_role" "ecs_task" {
  name               = "${var.app_name}-ecs-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

data "aws_iam_policy_document" "ecs_task_s3" {
  statement {
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.albums.arn,
      "${aws_s3_bucket.albums.arn}/*"
    ]
  }
}

resource "aws_iam_role_policy" "ecs_task_s3" {
  name   = "${var.app_name}-ecs-s3-policy"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_task_s3.json
}
