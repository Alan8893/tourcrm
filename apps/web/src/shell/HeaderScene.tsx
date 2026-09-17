import { selectHeaderScene } from "../assets/headerScenes";
import styles from "./HeaderScene.module.css";

export type HeaderSceneProps = {
  date?: Date;
};

export function HeaderScene({ date = new Date() }: HeaderSceneProps) {
  const scene = selectHeaderScene(date);
  if (!scene) return null;

  return (
    <picture className={styles.scene} aria-hidden="true">
      <source media="(max-width: 767.98px)" srcSet={scene.mobile} />
      <img className={styles.image} src={scene.desktop} alt="" />
    </picture>
  );
}
