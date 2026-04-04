resource "docker_image" "apache" {
  name = "httpd:2.4"
}

resource "docker_container" "apache" {
  name  = "apache"
  image = docker_image.apache.image_id

  ports {
    internal = 80
    external = 8585
  }

  restart_policy {
    condition = "always"
  }
}
