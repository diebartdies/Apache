output "apache_container_id" {
  description = "Docker container ID for Apache"
  value       = docker_container.apache.id
}

output "apache_container_name" {
  description = "Docker container name for Apache"
  value       = docker_container.apache.name
}

output "apache_access_url" {
  description = "URL to access Apache"
  value       = "http://localhost:8585"
}

